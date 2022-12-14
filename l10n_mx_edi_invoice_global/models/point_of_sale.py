# Part of Odoo. See LICENSE file for full copyright and licensing details.
from __future__ import division

import base64
import logging
from io import BytesIO
import requests

import re

from lxml import etree, objectify
from zeep import Client
from zeep.transports import Transport

from odoo import _, api, fields, models, tools
from odoo.exceptions import UserError
from odoo.tools import config
from odoo.tools.xml_utils import _check_with_xsd
from odoo.tools import float_round

_logger = logging.getLogger(__name__)


CFDI_TEMPLATE_33 = 'l10n_mx_edi_invoice_global.cfdiv33_pos'
#CFDI_XSLT_CADENA = 'l10n_mx_edi/data/3.3/cadenaoriginal.xslt'

CFDI_TEMPLATE_40 = 'l10n_mx_edi_invoice_global.cfdiv40_pos'
CFDI_XSLT_CADENA = 'l10n_mx_edi_invoice_global/data/4.0/cadenaoriginal_4_0.xslt'
CFDI_XSLT_CADENA_TFD = 'l10n_mx_edi_invoice_global/data/4.0/cadenaoriginal_TFD_1_1.xslt'
CFDI_SAT_QR_STATE = {
    'No Encontrado': 'not_found',
    'Cancelado': 'cancelled',
    'Vigente': 'valid',
}


def create_list_html(array):
    # Review if could be removed
    """Convert an array of string to a html list.
    :param array: A list of strings
    :type array: list
    :return: an empty string if not array, an html list otherwise.
    :rtype: str
    """
    if not array:
        return ''
    msg = ''
    for item in array:
        msg += '<li>' + item + '</li>'
    return '<ul>' + msg + '</ul>'


class WizardGlobalInvoice(models.TransientModel):
    _name = "wizard.global.invoice"

    name = fields.Char()

    def action_apply(self):
        print("YESSSSSSSS", self._context.get('active_ids'))
        res_ids = self._context.get('active_ids')
        print("IDSSSS: ", res_ids)
        orders = self.env["pos.order"].search([("id","in",res_ids)])
        orders.action_create_invoice()
        orders.action_validate_invoice()
        session = orders.session_id
        session._l10n_mx_edi_retry(p_orders=orders)
        for x in orders:
            print("ORDER: ", x.name)


class PosSession(models.Model):
    _name = 'pos.session'
    _inherit = ['pos.session', 'mail.thread']

    l10n_mx_edi_pac_status = fields.Selection(
        selection=[
            ('retry', 'Retry'),
            ('signed', 'Signed'),
            ('to_cancel', 'To cancel'),
            ('cancelled', 'Cancelled')
        ],
        string='PAC status',
        help='Refers to the status of the invoice inside the PAC.',
        readonly=True,
        copy=False)
    l10n_mx_edi_sat_status = fields.Selection(
        selection=[
            ('none', 'State not defined'),
            ('undefined', 'Not Synced Yet'),
            ('not_found', 'Not Found'),
            ('cancelled', 'Cancelled'),
            ('valid', 'Valid'),
        ],
        string='SAT status',
        help='Refers to the status of the invoice inside the SAT system.',
        readonly=True,
        copy=False,
        required=True,
        tracking=True,
        default='undefined')

    def l10n_mx_edi_update_pac_status(self):
        """Synchronize both systems: Odoo & PAC if the invoices need to be
        signed or cancelled.
        """
        for record in self:
            if record.l10n_mx_edi_pac_status == 'to_cancel':
                record.l10n_mx_edi_cancel()
            elif record.l10n_mx_edi_pac_status in ['retry', 'cancelled']:
                record._l10n_mx_edi_retry()

    def _l10n_mx_edi_retry(self, p_orders=False):
        """Generate and sign CFDI with version 3.3, just for the next cases:
        1.- The order was generated without customer, therefore without invoice
        2.- The order was generated with customer, but without invoice
        3.- The order is a refund and do not have invoice related"""
        self.ensure_one()
        orders = False
        if p_orders == False:
            orders = self.order_ids.filtered(
                lambda r: r.state != 'invoiced' and
                not r.l10n_mx_edi_cfdi_generated)
        else:
            orders = p_orders
        # skip orders in 0
        skip_orders = orders.filtered(lambda o: not o.lines)
        for order in orders:
            related = order._get_order_related()
            # Refund related in 0 or not refund or partial refund
            if not related or not related.lines or abs(order.amount_total) != abs(related.amount_total):
                continue
            # Refund related to a order not signed
            if (order.amount_total < 0 and not related.l10n_mx_edi_uuid) or (
                    # Full reversal
                    order.amount_total > 0 and related.session_id.state == 'closed'):
                skip_orders |= order
        orders -= skip_orders
        if skip_orders:
            olist = ' '.join(['<li>%s</li>' % (o) for o in skip_orders.mapped(
                'pos_reference')])
            msg_body = _("""The following orders were skipped because it's not
                         necessary to sign them:
                         <br><br><ul>%s</ul>""") % olist
            self.message_post(body=msg_body)
        partners = orders.mapped('partner_id').mapped(
            'commercial_partner_id').filtered(lambda r: r.vat)
        lambda_functions = (
            lambda r: r.amount_total > 0 and  # Order with partner
            r.partner_id and r.partner_id.commercial_partner_id.id
            not in partners.ids,
            lambda r: r.amount_total > 0 and not  # Order without Partner
            r.partner_id,
            lambda r: r.amount_total < 0 and  # Refund with partner
            r.partner_id and (r.partner_id.commercial_partner_id.id
                              not in partners.ids or not r.invoice_id),
            lambda r: r.amount_total < 0 and not  # Refund without Partner
            r.partner_id)
        signed = []
        self.l10n_mx_edi_pac_status = 'retry'
        attachment = self.env['ir.attachment']
        for func in lambda_functions:
            order_filter = orders.filtered(func)
            if not order_filter:
                continue
            cfdi_values = order_filter._l10n_mx_edi_create_cfdi()
            error = cfdi_values.pop('error', None)
            cfdi = cfdi_values.pop('cfdi', None)
            if error:
                self.message_post(body=error)
                signed.append(False)
                continue

            filename = order_filter.get_file_name()
            ctx = self.env.context.copy()
            ctx.pop('default_type', False)
            attachment_id = attachment.with_context(ctx).create({
                'name': '%s.xml' % filename,
                'res_id': self.id,
                'res_model': self._name,
                'datas': base64.encodebytes(cfdi),
                'description': _('Mexican PoS'),
                })
            self.message_post(
                body=_('CFDI document generated (may be not signed)'),
                attachment_ids=[attachment_id.id])

            cfdi_values = self._l10n_mx_edi_call_service('sign', cfdi)
            print("CFDI VALUES |||||||||||||||||||||||||||||||||||||| \n", cfdi, cfdi_values)
            if cfdi_values:
                self._l10n_mx_edi_post_sign_process(cfdi_values, order_filter)
                signed.append(bool(cfdi_values.get('cfdi_signed', False)))
            orders = orders - order_filter
        if all(signed):
            self.l10n_mx_edi_pac_status = 'signed'

    def _l10n_mx_edi_call_service(self, service_type, cfdi):
        """Call the right method according to the pac_name,
        it's info returned by the '_l10n_mx_edi_%s_info' % pac_name'
        method and the service_type passed as parameter.
        :param service_type: sign or cancel
        :type service_type: str
        :param cfdi: fiscal document
        :type cfdi: etree
        :return: the Result of the service called
        :rtype: dict
        """
        self.ensure_one()
        edi = self.env['account.edi.format']
        company_id = self.config_id.company_id
        pac_name = company_id.l10n_mx_edi_pac
        if not pac_name:
            return False
        # Get the informations about the pac
        credentials = getattr(edi, '_l10n_mx_edi_get_%s_credentials' % pac_name)(self.move_id)
        return getattr(edi, '_l10n_mx_edi_%s_%s_invoice' % (pac_name, service_type))(self.move_id, credentials, cfdi)

    def l10n_mx_edi_log_error(self, message):
        self.message_post(body=_('Error during the process: %s') % message)

    def _l10n_mx_edi_post_sign_process(self, cfdi_values, order_ids):
        """Post process the results of the sign service.
        :param cfdi_values: info of xml signed
        :type cfdi_values: dict
        :param order_ids: orders use to generate cfdi
        :type order_ids: pos.order
        """
        self.ensure_one()
        post_msg = []
        attach = []
        xml_signed = cfdi_values.get('cfdi_signed', '')
        code = cfdi_values.get('code', '')
        msg = cfdi_values.get('error', '')
        filename = order_ids.get_file_name()
        if xml_signed:
            body_msg = _('The sign service has been called with '
                         'success to %s') % filename
            # attach cfdi
            ctx = self.env.context.copy()
            ctx.pop('default_type', False)
            attachment_id = self.l10n_mx_edi_retrieve_last_attachment('%s.xml' % filename)
            attachment_id.write({
                'datas': base64.encodebytes(xml_signed),
                'description': 'Mexican invoice',
            })
            attach.extend([attachment_id.id])
            # Generate and attach pdf
            report = self.env.ref('l10n_mx_edi_invoice_global.l10n_mx_edi_report_session')
            xml = objectify.fromstring(xml_signed)
            data = self.move_id._l10n_mx_edi_decode_cfdi(xml_signed)
            data.update({'cfdi': xml})
            # The generation of report does not work in test environment
            # because of this issue https://github.com/odoo/odoo/issues/18841
            if not config['test_enable']:
                pdf, ext = report._render_qweb_pdf(self.ids, data)
                attachment_id = self.env[
                    'ir.attachment'].with_context(ctx).create({
                        'name': '%s.%s' % (filename, ext),
                        'res_id': self.id,
                        'res_model': self._name,
                        'datas': base64.b64encode(pdf),
                        'description': 'Printed representation of the CFDI',
                    })
                attach.extend([attachment_id.id])
            uuid = self.l10n_mx_edi_get_tfd_etree(xml).get('UUID', '')
            order_ids.write({
                'l10n_mx_edi_cfdi_generated': True, 'l10n_mx_edi_uuid': uuid})
        else:
            body_msg = _('The sign service requested failed to %s') % filename
        if code:
            post_msg.extend([_('Code: ') + str(code)])
        if msg:
            post_msg.extend([_('Message: ') + msg])
        self.message_post(
            body=body_msg + create_list_html(post_msg),
            attachment_ids=attach)

    def _l10n_mx_edi_post_cancel_process(self, cfdi_values, order_ids, attach):
        """Post process the results of the cancel service.
        :param cfdi_values: info of xml signed
        :type cfdi_values: dict
        :param order_ids: orders use to generate cfdi
        :type order_ids: pos.order
        :param attach: file attachment in invoice
        :type attach: ir.attachment
        """

        self.ensure_one()
        cancelled = cfdi_values.get('cancelled', '')
        code = cfdi_values.get('code', '')
        msg = cfdi_values.get('msg', '')
        filename = cfdi_values.get('filename', '')
        if cancelled:
            body_msg = _('The cancel service has been called with success '
                         'to %s') % filename
            order_ids.write({'l10n_mx_edi_cfdi_generated': False})
            attach.name = 'cancelled_%s' % '_'.join(
                filename.split('_')[-2:])
        else:
            body_msg = _(
                'The cancel service requested failed to %s') % filename
        post_msg = []
        if code:
            post_msg.extend([_('Code: ') + str(code)])
        if msg:
            post_msg.extend([_('Message: ') + msg])
        self.message_post(body=body_msg + create_list_html(post_msg))

    """
    def action_pos_session_close(self, balancing_account=False, amount_to_balance=0, bank_payment_method_diffs=None): 
        orders = self.order_ids.filtered(
            lambda r: r.state != 'invoiced' and
            not r.l10n_mx_edi_cfdi_generated and
            r.partner_id and r.partner_id.vat)
        orders.action_create_invoice()
        orders.action_validate_invoice()
        res = super(PosSession, self).action_pos_session_close()
        self._l10n_mx_edi_retry()
        return res
    """

    def l10n_mx_edi_cancel(self):
        """If the session have XML documents, try send to cancel in SAT system
        """
        att_obj = self.env['ir.attachment']
        for record in self:
            attach_xml_ids = att_obj.search([
                ('name', 'ilike', '%s%%.xml' % record.name.replace('/', '_')),
                ('res_model', '=', record._name),
                ('res_id', '=', record.id),
            ])
            cancel = []
            self.l10n_mx_edi_pac_status = 'to_cancel'
            for att in attach_xml_ids.filtered('datas'):
                cfdi_values = self._l10n_mx_edi_call_service(
                    'cancel', att.datas)
                if not cfdi_values:
                    cancel.append([False])
                    continue
                orders = self.order_ids.filtered(
                    lambda r: not r.account_move and
                    r.l10n_mx_edi_cfdi_generated)
                func = (lambda r: r.partner_id) if _(
                    'with_partner') in att.name else (
                        lambda r: not r.partner_id)
                order_ids = orders.filtered(func)
                cfdi_values.update({'filename': att.name})
                self._l10n_mx_edi_post_cancel_process(
                    cfdi_values, order_ids, att)
                cancel.append(cfdi_values.get('cancelled', False))
            if all(cancel):
                self.l10n_mx_edi_pac_status = 'cancelled'

    # -------------------------------------------------------------------------
    # SAT/PAC service methods
    # -------------------------------------------------------------------------

    def _l10n_mx_edi_solfact_sign(self, pac_info, cfdi):
        """SIGN for Solucion Factible.
        """
        url = pac_info['url']
        username = pac_info['username']
        password = pac_info['password']
        try:
            transport = Transport(timeout=20)
            client = Client(url, transport=transport)
            response = client.service.timbrar(username, password, cfdi, False)
        except BaseException as e:
            return {'error': 'Error during the process', 'code': str(e)}
        res = response.resultados
        msg = getattr(res[0] if res else response, 'mensaje', None)
        code = getattr(res[0] if res else response, 'status', None)
        xml_signed = getattr(res[0] if res else response, 'cfdiTimbrado', None)
        print("MSG: ", res)
        if xml_signed:
            return {'cfdi': base64.b64encode(xml_signed)}
        return {'error': msg, 'code': code}

    def _l10n_mx_edi_solfact_cancel(self, pac_info, cfdi):
        """CANCEL for Solucion Factible.
        """
        url = pac_info['url']
        username = pac_info['username']
        password = pac_info['password']
        xml_string = base64.b64decode(cfdi)
        xml = objectify.fromstring(xml_string)
        uuids = [self.l10n_mx_edi_get_tfd_etree(xml).get('UUID', '')]
        company_id = self.config_id.company_id
        certificate_ids = company_id.l10n_mx_edi_certificate_ids
        certificate_id = certificate_ids.sudo().get_valid_certificate()
        cer_pem = base64.b64encode(certificate_id.get_pem_cer(
            certificate_id.content)).decode('UTF-8')
        key_pem = base64.b64encode(certificate_id.get_pem_key(
            certificate_id.key, certificate_id.password)).decode('UTF-8')
        key_password = certificate_id.password
        try:
            transport = Transport(timeout=20)
            client = Client(url, transport=transport)
            response = client.service.cancelar(username, password, uuids,
                                               cer_pem, key_pem, key_password)
        except BaseException as e:
            self.l10n_mx_edi_log_error(str(e))
            return {}
        res = response.resultados
        code = getattr(res[0], 'statusUUID', None) if res else getattr(
            response, 'status', None)
        cancelled = code in ('201', '202')  # cancelled or previously cancelled
        # no show code and response message if cancel was success
        msg = '' if cancelled else getattr(
            res[0] if res else response, 'mensaje', None)
        code = '' if cancelled else code
        return {'cancelled': cancelled, 'code': code, 'msg': msg}

    def _l10n_mx_edi_finkok_sign(self, pac_info, cfdi):
        """SIGN for Finkok.
        """
        url = pac_info['url']
        username = pac_info['username']
        password = pac_info['password']
        try:
            transport = Transport(timeout=20)
            client = Client(url, transport=transport)
            response = client.service.stamp(cfdi, username, password)
        except Exception as e:
            return {'error': 'Error during the process', 'code': str(e)}
        code = 0
        msg = None
        if response.Incidencias:
            code = getattr(response.Incidencias.Incidencia[0], 'CodigoError', None)
            msg = getattr(response.Incidencias.Incidencia[0], 'MensajeIncidencia', None)
            return {'error': msg, 'code': code}

        xml_signed = getattr(response, 'xml', None)
        xml_signed = base64.b64encode(xml_signed.encode('utf-8'))
        return {'cfdi': xml_signed}

    def _l10n_mx_edi_finkok_cancel(self, pac_info, cfdi):
        """CANCEL for Finkok.
        """
        url = pac_info['url']
        username = pac_info['username']
        password = pac_info['password']
        xml_string = base64.b64decode(cfdi)
        xml = objectify.fromstring(xml_string)
        uuid = self.l10n_mx_edi_get_tfd_etree(xml).get('UUID', '')
        if not uuid:
            return {}
        company_id = self.config_id.company_id
        certificate_ids = company_id.l10n_mx_edi_certificate_ids
        certificate_id = certificate_ids.sudo().get_valid_certificate()
        cer_pem = certificate_id.get_pem_cer(
            certificate_id.content)
        key_pem = certificate_id.get_pem_key(
            certificate_id.key, certificate_id.password)
        cancelled = False
        code = False
        try:
            transport = Transport(timeout=20)
            client = Client(url, transport=transport)
            uuid_type = client.get_type('ns0:stringArray')()
            uuid_type.string = [uuid]
            invoices_list = client.get_type('ns1:UUIDS')(uuid_type)
            response = client.service.cancel(
                invoices_list, username, password, company_id.vat, cer_pem, key_pem)
        except BaseException as e:
            self.l10n_mx_edi_log_error(str(e))
            return {}
        if not getattr(response, 'Folios', None):
            code = getattr(response, 'CodEstatus', None)
            msg = _("Cancelling got an error") if code else _(
                'A delay of 2 hours has to be respected before to cancel')
        else:
            code = getattr(response.Folios.Folio[0], 'EstatusUUID', None)
            # cancelled or previously cancelled
            cancelled = code in ('201', '202')
            # no show code and response message if cancel was success
            code = '' if cancelled else code
            msg = '' if cancelled else _("Cancelling got an error")
        return {'cancelled': cancelled, 'code': code, 'msg': msg}

    @api.model
    def l10n_mx_edi_retrieve_attachments(self, filename):
        """Retrieve all the cfdi attachments generated for this session
        :return: An ir.attachment recordset
        :rtype: ir.attachment()
        """
        self.ensure_one()
        domain = [
            ('res_id', '=', self.id),
            ('res_model', '=', self._name),
            ('name', '=', filename)]
        return self.env['ir.attachment'].search(domain)

    @api.model
    def l10n_mx_edi_retrieve_last_attachment(self, filename):
        attachment_ids = self.l10n_mx_edi_retrieve_attachments(filename)
        return attachment_ids[0] if attachment_ids else None

    def l10n_mx_edi_amount_to_text(self, amount_total):
        """Method to transform a float amount to text words
        E.g. 100 - ONE HUNDRED
        :returns: Amount transformed to words mexican format for invoices
        :rtype: str
        """
        self.ensure_one()
        currency = self.currency_id.name.upper()
        # M.N. = Moneda Nacional (National Currency)
        # M.E. = Moneda Extranjera (Foreign Currency)
        currency_type = 'M.N' if currency == 'MXN' else 'M.E.'
        # Split integer and decimal part
        amount_i, amount_d = divmod(amount_total, 1)
        amount_d = round(amount_d, 2)
        amount_d = int(round(amount_d * 100, 2))
        words = self.currency_id.with_context(
            lang=self.company_id.partner_id.lang or 'es_ES').amount_to_text(
                amount_i).upper()
        invoice_words = '%(words)s %(amount_d)02d/100 %(curr_t)s' % dict(
            words=words, amount_d=amount_d, curr_t=currency_type)
        return invoice_words

    def l10n_mx_edi_update_sat_status(self):
        """Synchronize both systems: Odoo & SAT to make sure the invoice is valid."""
        url = 'https://consultaqr.facturaelectronica.sat.gob.mx/ConsultaCFDIService.svc?wsdl'
        headers = {'SOAPAction': 'http://tempuri.org/IConsultaCFDIService/Consulta', 'Content-Type': 'text/xml; charset=utf-8'}  # noqa
        template = """<?xml version="1.0" encoding="UTF-8"?>
<SOAP-ENV:Envelope xmlns:ns0="http://tempuri.org/" xmlns:ns1="http://schemas.xmlsoap.org/soap/envelope/" xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance"
 xmlns:SOAP-ENV="http://schemas.xmlsoap.org/soap/envelope/">
   <SOAP-ENV:Header/>
   <ns1:Body>
      <ns0:Consulta>
         <ns0:expresionImpresa>${data}</ns0:expresionImpresa>
      </ns0:Consulta>
   </ns1:Body>
</SOAP-ENV:Envelope>"""  # noqa
        namespace = {'a': 'http://schemas.datacontract.org/2004/07/Sat.Cfdi.Negocio.ConsultaCfdi.Servicio'}
        att_obj = self.env['ir.attachment']
        for record in self:
            attach_xml_ids = att_obj.search([
                ('name', 'ilike', '%s%%.xml' % record.name.replace('/', '_')),
                ('res_model', '=', record._name),
                ('res_id', '=', record.id),
            ])
            for att in attach_xml_ids.filtered('datas'):
                xml = objectify.fromstring(base64.b64decode(att.datas))
                supplier_rfc = xml.Emisor.get('Rfc')
                customer_rfc = xml.Receptor.get('Rfc')
                total = xml.get('Total')
                uuid = self.l10n_mx_edi_get_tfd_etree(xml).get('UUID', '')
                params = '?re=%s&amp;rr=%s&amp;tt=%s&amp;id=%s' % (
                    tools.html_escape(tools.html_escape(supplier_rfc or '')),
                    tools.html_escape(tools.html_escape(customer_rfc or '')),
                    total or 0.0, uuid or '')
                soap_env = template.format(data=params)
                try:
                    soap_xml = requests.post(url, data=soap_env,
                                             headers=headers, timeout=20)
                    response = objectify.fromstring(soap_xml.text)
                    status = response.xpath(
                        '//a:Estado', namespaces=namespace)
                except Exception as e:
                    record.l10n_mx_edi_log_error(str(e))
                    continue
                record.l10n_mx_edi_sat_status = CFDI_SAT_QR_STATE.get(
                    status[0] if status else '', 'none')

    @api.model
    def l10n_mx_edi_get_tfd_etree(self, cfdi):
        """Get the TimbreFiscalDigital node from the cfdi."""
        if not hasattr(cfdi, 'Complemento'):
            return None
        attribute = 'tfd:TimbreFiscalDigital[1]'
        namespace = {'tfd': 'http://www.sat.gob.mx/TimbreFiscalDigital'}
        node = cfdi.Complemento.xpath(attribute, namespaces=namespace)
        return node[0] if node else None


class PosOrder(models.Model):
    _inherit = 'pos.order'

    l10n_mx_edi_cfdi_generated = fields.Boolean(
        'XML Generated', copy=False,
        help='Indicate if this order was consider in the session XML')
    l10n_mx_edi_uuid = fields.Char(
        'Fiscal Folio', copy=False, index=True,
        help='Folio in electronic document, returned by SAT.')

    global_invoice_id = fields.Many2one("global.invoice")

    journal_id = fields.Many2one(
        comodel_name='account.journal',
        string='Diario',
        required=False,
        related="session_id.config_id.journal_id")


    payment_method_id = fields.Many2one("pos.payment.method",
                                        compute="_compute_payment_method_id")

    def _compute_payment_method_id(self):
        rec = [line.payment_method_id for line in self.payment_ids]
        line1 = rec[0] if rec else ''
        print("METODO: ", line1)
        self.payment_method_id = line1.id if line1 != '' else ''

    def _get_order_related(self):
        self.ensure_one()
        return self.search([
            ('pos_reference', '=', self.pos_reference), ('id', '!=', self.id),
            ('partner_id', '=', self.partner_id.id)], limit=1)

    def get_file_name(self):
        """Return the file name, with a consecutive to duplicated names.
        Params:
            partner: Receive True if the file to generate contain the records
            that have partner reference, to set in the fail the label
            'with_partner'
            inc: Indicate if must be add the consecutive"""
        partner = self.mapped('partner_id')
        doc_type = self.filtered(lambda r: r.amount_total < 0)
        type_rec = _('with_partner') if partner else _('wo_partner')
        egre = '' if not doc_type else _('_refund')
        session = self.mapped('session_id')
        session_name = session.name.replace('/', '_')
        fname = "%s_%s%s" % (session_name, type_rec, egre)

        count = self.env['ir.attachment'].search_count([
            ('name', '=', fname),
            ('res_model', '=', session._name),
            ('res_id', '=', session.id),
        ])
        if count > 0:
            fname = "%s_%s%s_%s" % (
                session_name, type_rec, egre, count + 1)
        return fname

    def _get_subtotal_wo_discount(self, precision_digits, line):
        return float(line.price_subtotal / (1 - abs(line.discount/100)) if
                     line.discount != 100 else abs(line.price_unit * line.qty))

    def _get_discount(self, precision_digits, line):
        return float(self._get_subtotal_wo_discount(precision_digits, line) -
                     abs(line.price_subtotal)) if line.discount else False
        
    def _l10n_mx_edi_clean_to_legal_name(self, name):
        """ We remove the SA de CV / SL de CV / S de RL de CV as they are never in the official name in the XML"""
        res = re.sub(r"(?i:\s+(s\.?[al]\.?|s\.? de r\.?l\.?)"
                     r"( de c\.?v\.?|))\s*$", "", name).upper()
        return res

    def _l10n_mx_edi_create_cfdi_values(self, folio=False, periodo=False, mes=False, yearg=False):
        """Generating the base dict with data needed to generate the electronic
        document
        :return: Base data to generate electronic document
        :rtype: dict
        """
        session = self.mapped('session_id')
        invoice_obj = self.env['account.move']
        precision_digits = 6
        company_id = session.config_id.company_id

        invoice = {
            'record': self,
            'invoice': invoice_obj,
            'currency': session.currency_id.name,
            'supplier': company_id,
            'supplier_name': self._l10n_mx_edi_clean_to_legal_name(company_id.name),
            'folio': session.name if not folio else folio,
            'serie': 'NA',
            'periodicidad': periodo, 
            'meses': mes, 
            'yearg': yearg
        }
        invoice['subtotal_wo_discount'] = '%.*f' % (precision_digits, sum([
            self._get_subtotal_wo_discount(precision_digits, line) for line in
            self.mapped('lines')]))
        invoice['amount_untaxed'] = abs(float_round(sum(
            [self._get_subtotal_wo_discount(precision_digits, p) for p in
             self.mapped('lines')]), 2))
        invoice['amount_discount'] = '%.*f' % (precision_digits, sum([
            float(self._get_discount(precision_digits, p)) for p in
            self.mapped('lines')]))

        invoice['tax_name'] = lambda t: {
            'ISR': '001', 'IVA': '002', 'IEPS': '003'}.get(t, False)
        invoice['taxes'] = self._l10n_mx_edi_create_taxes_cfdi_values()

        invoice['amount_total'] = abs(float_round(float(
            invoice['amount_untaxed']), 2) - round(float(
                invoice['amount_discount'] or 0), 2) + (round(
                    invoice['taxes']['total_transferred'] or 0, 2)) - (round(
                        invoice['taxes']['total_withhold'] or 0, 2)))
        invoice['document_type'] = 'I' if self.filtered(
            lambda r: r.amount_total > 0) else 'E'
        payment_ids = self.mapped('payment_ids').filtered(
            lambda st: st.amount > 0)

        # Get highest amount payment
        payments = payment_ids.read_group(
            [('id', 'in', payment_ids.ids)],
            ['amount'],
            'payment_method_id'
        )
        payments = sorted(payments, key=lambda i: i['amount'], reverse=True)
        payment_id = payments[0]['payment_method_id'][0] if payments else False
        invoice['payment_method'] = self.env['pos.payment.method'].browse(
            payment_id).l10n_mx_edi_payment_method_id.code if payment_id else '99'
        return invoice

    def get_cfdi_related(self):
        """To node CfdiRelacionados get documents related with that order
        Considered:
            - Order Refund
            - Order Cancelled"""
        cfdi_related = []
        refund = self.filtered(
            lambda a: a.amount_total < 0 and not a.l10n_mx_edi_uuid)
        relation_type = '04' if self[0].l10n_mx_edi_uuid else (
            '01' if refund else '')
        for order in refund:
            origin = self.search(
                [('pos_reference', '=', order.pos_reference),
                 ('id', '!=', order.id),
                 ('partner_id', '=', order.partner_id.id),
                 ('date_order', '<=', order.date_order)], limit=1)
            cfdi_related += [origin.l10n_mx_edi_uuid] if origin else ()
        cfdi_related += [
            i.l10n_mx_edi_uuid for i in self if i.l10n_mx_edi_uuid]
        if not cfdi_related:
            return {}
        return {
            'type': relation_type,
            'related': [x for x in set(cfdi_related)],
            }

    def _l10n_mx_edi_create_cfdi(self, folio=False, periodo=False, mes=False, yearg=False):
        """Creates and returns a dictionnary containing 'cfdi' if the cfdi is
        well created, 'error' otherwise."""
        if not self:
            return {}
        qweb = self.env['ir.qweb']
        invoice_obj = self.env['account.move']
        company_id = self.mapped('company_id')
        error_log = []
        pac_name = company_id.l10n_mx_edi_pac

        values = self._l10n_mx_edi_create_cfdi_values(folio, periodo, mes, yearg)

        # -Check certificate
        certificate_ids = company_id.l10n_mx_edi_certificate_ids
        certificate_id = certificate_ids.sudo().get_valid_certificate()
        if not certificate_id:
            error_log.append(_('No valid certificate found'))

        # -Check PAC
        if pac_name:
            pac_test_env = company_id.l10n_mx_edi_pac_test_env
            pac_password = company_id.l10n_mx_edi_pac_password
            if not pac_test_env and not pac_password:
                error_log.append(_('No PAC credentials specified.'))
        else:
            error_log.append(_('No PAC specified.'))

        if error_log:
            return {'error': _('Please check your configuration: ') +
                    create_list_html(error_log)}

        tz = invoice_obj._l10n_mx_edi_get_cfdi_partner_timezone(values['supplier'])
        values['certificate_number'] = certificate_id.serial_number
        values['certificate'] = certificate_id.sudo().get_data()[0].decode('utf-8')
        values['date'] = fields.datetime.now(tz).strftime('%Y-%m-%dT%H:%M:%S')

        cfdi = qweb._render(CFDI_TEMPLATE_40, values=values)
        attachment = False #self.env.ref('l10n_mx_edi.xsd_cached_cfdv33_xsd', False)
        xsd_datas = base64.b64decode(attachment.datas) if attachment else b''
        # -Compute cadena
        tree = objectify.fromstring(cfdi)
        cadena_root = etree.parse(tools.file_open(CFDI_XSLT_CADENA))
        cadena = str(etree.XSLT(cadena_root)(tree))
        tree.attrib['Sello'] = certificate_id.sudo().get_encrypted_cadena(
            cadena)

        # Check with xsd
        if xsd_datas:
            try:
                with BytesIO(xsd_datas) as xsd:
                    _check_with_xsd(tree, xsd)
            except (IOError, ValueError):
                _logger.info(_('The xsd file to validate the XML structure '
                               'was not found'))
            except BaseException as e:
                return {'error': (_('The cfdi generated is not valid') +
                                  create_list_html(str(e).split('\\n')))}

        return {'cfdi': etree.tostring(
            tree, pretty_print=True, xml_declaration=True, encoding='UTF-8')}

    def _l10n_mx_edi_create_taxes_cfdi_values(self):
        """Create the taxes values to fill the CFDI template.
        """
        values = {
            'total_withhold': 0,
            'total_transferred': 0,
            'withholding': [],
            'transferred': [],
        }
        taxes = {}
        tag_model = self.env['account.account.tag']
        for line in self.mapped('lines').filtered('price_subtotal'):
            price = line.price_unit * (1.0 - (line.discount or 0.0) / 100.0)
            tax_line = {tax['id']: tax for
                        tax in line.tax_ids_after_fiscal_position.compute_all(
                            price, line.order_id.pricelist_id.currency_id,
                            abs(line.qty), line.product_id,
                            line.order_id.partner_id)['taxes']}
            for tax in line.tax_ids_after_fiscal_position.filtered(
                    lambda r: r.l10n_mx_tax_type != 'Exento'):
                tax_dict = tax_line.get(tax.id, {})
                amount = abs(tax_dict.get('amount', tax.amount / 100 *
                                          line.price_subtotal))
                rate = round(abs(tax.amount), 2)
                base = float(amount) / float(rate) if \
                    tax.amount_type == 'fixed' else tax_dict.get(
                        'base', line.price_subtotal)

                """
                tags = tax_dict['tag_ids']
                
                tag = (tags and tags[0] and tag_model.browse(tags[0]).name or tax.name).upper()
                """

                tag = False
                if 'IEPS' in tax.name:
                    tag = 'IEPS'
                elif 'ISR' in tax.name:
                    tag = 'ISR'
                else:
                    tag = 'IVA'
                tax_key = '%s%s' % (tag or 'NA', str(tax.amount))
                if tax_key not in taxes:
                    taxes.update({tax_key: {
                        'name': tag,
                        'amount': round(base * (rate if tax.amount_type == 'fixed'
                                          else rate / 100.0),4),
                        'rate': (rate if tax.amount_type == 'fixed' else
                                 rate / 100.0),
                        'type': tax.l10n_mx_tax_type,
                        'tax_amount': round(tax_dict.get('amount', tax.amount),4),
                        'base': base,
                    }})
                else:
                    taxes[tax_key].update({
                        'amount': round(taxes[tax_key]['amount'] + (
                            base * (rate if tax.amount_type == 'fixed' else
                                    rate / 100.0)),4),
                        'base': taxes[tax_key]['base'] + base,
                    })
                if tax.amount >= 0:
                    values['total_transferred'] += base * (
                        rate if tax.amount_type == 'fixed' else rate / 100.0)
                else:
                    values['total_withhold'] += base * (
                        rate if tax.amount_type == 'fixed' else rate / 100.0)
        values['transferred'] = [tax for tax in taxes.values() if tax[
            'tax_amount'] >= 0]
        values['withholding'] = [tax for tax in taxes.values() if tax[
            'tax_amount'] < 0]
        return values

    def action_create_invoice(self):
        """When is created a new register, verify that partner have VAT, and
        create automatically the invoice."""
        for order in self.filtered(
                lambda ord: ord.partner_id.vat and not ord.account_move):
            order.action_pos_order_invoice()

    def action_validate_invoice(self):
        """Validate the invoice after of opened."""
        self.mapped('account_move').filtered(
            lambda inv: inv.state == 'draft').action_post()

    def action_pos_order_paid(self):
        """Create Invoice if have partner with VAT"""
        res = super(PosOrder, self).action_pos_order_paid()
        #self.action_create_invoice()
        self.filtered(
            lambda r: r.account_move.state == 'draft').action_validate_invoice()  # noqa
        return res

    def _get_main_order(self):
        """Used to get the main order that generated the return that you want
        to generate the invoice
        :return: The possible order that generated the return
        :rtype: pos.order()
        """
        self.ensure_one()
        return self.search([
            ('partner_id', '=', self.partner_id.id),
            ('sale_journal', '=', self.sale_journal.id),
            ('account_move', 'not in', [False, self.account_move.id]),
            ('pos_reference', '=', self.pos_reference),
        ], order='date_order DESC', limit=1)

    def prepare_credit_note(self):
        """Prepare the main values needed to create the credit note
        :return: The values of the new invoice
        :rtype: dict
        """
        self.ensure_one()
        if not self.partner_id:
            raise UserError(_('Please provide a partner for the sale.'))
        return {
            'name': self.name,
            'origin': self.name,
            'account_id': self.partner_id.property_account_receivable_id.id,
            'journal_id': self.sale_journal.id or None,
            'type': 'out_refund',
            'reference': self.name,
            'partner_id': self.partner_id.id,
            'comment': self.note or '',
            'currency_id': self.pricelist_id.currency_id.id,
            'company_id': self.company_id.id,
        }

    def add_payment_for_credit_note(self):
        """Generate a payment for a credit note
        :return: boolean
        :rtype: dict
        TODO - Could be removed the method?
        """
        self.ensure_one()
        main_order = self._get_main_order()
        if main_order.account_move.state != 'posted':
            return self.account_move.action_post()
        invoice = main_order.account_move
        if invoice.l10n_mx_edi_cfdi_uuid:
            self.account_move.l10n_mx_edi_origin = '%s|%s' % (
                '01', invoice.l10n_mx_edi_cfdi_uuid)
        self.account_move.action_post()
        if invoice.state == 'paid':
            journals = invoice.line_ids.filtered("reconciled")
            journals.remove_move_reconcile()
        return True

    def action_pos_order_invoice(self):
        """Create a credit note if the order is a return of products"""
        res_invoice = super(PosOrder, self).action_pos_order_invoice()
        refunds = self.filtered(lambda r: r.amount_total < 0)
        if refunds:
            refunds.add_payment_for_credit_note()
        return res_invoice

    def _prepare_invoice(self):
        res = super(PosOrder, self)._prepare_invoice()
        statement_ids = self.mapped('statement_ids').filtered(
            lambda st: st.amount > 0)
        journal_ids = statement_ids.read_group([
            ('id', 'in', statement_ids.ids)], ['journal_id'], 'journal_id')
        max_count = 0
        journal_id = False
        for journal in journal_ids:
            if journal.get('journal_id_count') > max_count:
                max_count = journal.get('journal_id_count')
                journal_id = journal.get('journal_id')[0]
        journal = self.env['account.journal'].browse(journal_id) if journal_id else False  # noqa
        if journal and journal.l10n_mx_edi_payment_method_id:
            res['l10n_mx_edi_payment_method_id'] = journal.l10n_mx_edi_payment_method_id.id  # noqa
            context = self.env.context.copy()
            context['force_payment_method'] = res[
                'l10n_mx_edi_payment_method_id']
            self.env.context = context
        return res
