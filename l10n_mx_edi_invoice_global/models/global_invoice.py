from odoo import fields, models, api, tools, _
import logging
from odoo.exceptions import UserError, ValidationError

from lxml import etree, objectify
from zeep import Client
from zeep.transports import Transport

from odoo.tools import config

import base64
import requests

from lxml import etree
from lxml.objectify import fromstring

CFDI_XSLT_CADENA = 'l10n_mx_edi/data/3.3/cadenaoriginal.xslt'
CFDI_XSLT_CADENA_TFD = 'l10n_mx_edi/data/xslt/3.3/cadenaoriginal_TFD_1_1.xslt'

_logger = logging.getLogger(__name__)


CFDI_TEMPLATE_33 = 'l10n_mx_edi_invoice_global.cfdiv33_pos'
CFDI_TEMPLATE_40 = 'l10n_mx_edi_invoice_global.cfdiv40_pos'
CFDI_XSLT_CADENA = 'l10n_mx_edi_invoice_global/data/4.0/cadenaoriginal.xslt'
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

class AccountEdiFormat(models.Model):
    _inherit = 'account.edi.format'

    def _l10n_mx_edi_get_values_finkok_credentials(self, company):
        return self._l10n_mx_edi_get_finkok_credentials_company(company)

    def _l10n_mx_edi_get_finkok_credentials_company(self, company):
        ''' Return the company credentials for PAC: finkok. Does not depend on a recordset
        '''
        if company.l10n_mx_edi_pac_test_env:
            return {
                'username': 'cfdi@vauxoo.com',
                'password': 'vAux00__',
                'sign_url': 'http://demo-facturacion.finkok.com/servicios/soap/stamp.wsdl',
                'cancel_url': 'http://demo-facturacion.finkok.com/servicios/soap/cancel.wsdl',
            }
        else:
            if not company.l10n_mx_edi_pac_username or not company.l10n_mx_edi_pac_password:
                return {
                    'errors': [_("The username and/or password are missing.")]
                }

            return {
                'username': company.l10n_mx_edi_pac_username,
                'password': company.l10n_mx_edi_pac_password,
                'sign_url': 'http://facturacion.finkok.com/servicios/soap/stamp.wsdl',
                'cancel_url': 'http://facturacion.finkok.com/servicios/soap/cancel.wsdl',
            }

    def _l10n_mx_edi_get_values_solfact_credentials(self, company):
        return self._l10n_mx_edi_get_solfact_credentials_company(company)

    def _l10n_mx_edi_get_solfact_credentials_company(self, company):
        ''' Return the company credentials for PAC: solucion factible. Does not depend on a recordset
        '''
        if company.l10n_mx_edi_pac_test_env:
            return {
                'username': 'testing@solucionfactible.com',
                'password': 'timbrado.SF.16672',
                'url': 'https://testing.solucionfactible.com/ws/services/Timbrado?wsdl',
            }
        else:
            if not company.l10n_mx_edi_pac_username or not company.l10n_mx_edi_pac_password:
                return {
                    'errors': [_("The username and/or password are missing.")]
                }

            return {
                'username': company.l10n_mx_edi_pac_username,
                'password': company.l10n_mx_edi_pac_password,
                'url': 'https://solucionfactible.com/ws/services/Timbrado?wsdl',
            }


class GlobalInvoice(models.Model):
    _name = 'global.invoice'
    _inherit = ['mail.thread']
    _description = 'Registro de facturacion global'

    name = fields.Char(string='Session ID', required=True, readonly=True, default='/')
    start_date = fields.Date(
        string='Fecha Inicio',
        required=False)
    end_date = fields.Date(
        string='Fecha Final',
        required=False)
    journal_id = fields.Many2one(
        comodel_name='account.journal',
        string='Diario',
        required=False)
    
        
    periodicidad =  fields.Selection([('01','Diario'),
                                      ('02','Semanal'),
                                      ('03','Quincenal'),
                                      ('04','Mensual'),
                                      ('05','Bimestral')], 
                                     string="Periodicidad", required=True)
    meses = fields.Selection([('01','Enero'),
                              ('02','Febrero'),
                              ('03','Marzo'),
                              ('04','Abril'),
                              ('05','Mayo'),
                              ('06','Junio'),
                              ('07','Julio'),
                              ('08','Agosto'),
                              ('09','Septiembre'),
                              ('10','Octubre'),
                              ('11','Noviembre'),
                              ('12','Diciembre')], 
                             string="Mes")
    bmeses = fields.Selection([('13','Enero-Febrero'),
                               ('14','Marzo-Abril'),
                               ('15','Mayo-Junio'),
                               ('16','Julio-Agosto'),
                               ('17','Septiembre-Octubre'),
                               ('18','Noviembre-Diciembre')], 
                              string="Bimestre")
    yearg = fields.Char(string="Año", required=True)
    


    payment_method_id = fields.Many2one("pos.payment.method", string="Metodo de pago")
    l10n_mx_edi_uuid = fields.Char(
        'Fiscal Folio', copy=False, index=True,
        help='Folio in electronic document, returned by SAT.')

    global_invoice_ids = fields.One2many(
        comodel_name='global.invoice.line',
        inverse_name='global_invoice_id',
        string='Lineas Factura Global',
        required=False)

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

    def _compute_company_id(self):
        return self.env.company

    company_id = fields.Many2one(comodel_name='res.company', string='Company',
                                 default=_compute_company_id, store=True)
    
    
    currency_id = fields.Many2one('res.currency',
        related="company_id.currency_id",
        string='Moneda')




    def l10n_mx_edi_update_pac_status(self):
        """Synchronize both systems: Odoo & PAC if the invoices need to be
        signed or cancelled.
        """
        for record in self:
            if record.l10n_mx_edi_pac_status == 'to_cancel':
                record.l10n_mx_edi_cancel()
            elif record.l10n_mx_edi_pac_status in ['retry']:
                record._l10n_mx_edi_retry()
            else:
                print("NOOOOOOOOOOOOOOOo")

    def l10n_mx_edi_sign_ginvoice(self):
        for rec in self:
            rec._l10n_mx_edi_retry()

    def load_table_lines(self):
        for rec in self:
            if rec.start_date == False or rec.end_date == False:
                raise UserError(_("La feha inicio y fin deben estar llenas."))
            for x in rec.global_invoice_ids:
                    x.pos_order_id.global_invoice_id = False
            rec.global_invoice_ids.unlink()
            orders_1 = self.env["pos.order"].search([('account_move','=',False),
                                                   ('global_invoice_id','=',False),
                                                   ('date_order','>=',rec.start_date),
                                                   ('date_order','<=',rec.end_date),
                                                   ('journal_id','=',rec.journal_id.id),
                                                   ('company_id','=',rec.company_id.id)])
            orders = []
            for x in orders_1:
                o = [line.payment_method_id for line in x.payment_ids]
                line1 = o[0]
                if line1.id == rec.payment_method_id.id:
                    orders.append(x)
            line = [(6, 0, [])]
            for o in orders[::-1]:
                vals = {
                    'pos_order_id': o.id,
                }

                line.append((0, 0, vals))
            self.global_invoice_ids = line

    @api.model
    def create(self, values):
        ctx = dict(self.env.context, company_id=self.env.company)

        pos_name = self.env['ir.sequence'].with_context(ctx).next_by_code('pos.session')
        if values.get('name'):
            pos_name += ' ' + values['name']

        values.update({
            'name': pos_name,
        })
        res = super(GlobalInvoice, self).create(values)

        return res


    def _l10n_mx_edi_retry(self):
        """Generate and sign CFDI with version 3.3, just for the next cases:
        1.- The order was generated without customer, therefore without invoice
        2.- The order was generated with customer, but without invoice
        3.- The order is a refund and do not have invoice related"""
        self.ensure_one()
        olist = []
        for x in self.global_invoice_ids:
            olist.append(x.pos_order_id.id)
        orders = self.env['pos.order'].search([("id", "in", olist)])
        skip_orders = orders.filtered(lambda o: not o.lines)
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
                                        not in partners.ids or not r.account_move),
            lambda r: r.amount_total < 0 and not  # Refund without Partner
            r.partner_id)
        signed = []
        self.l10n_mx_edi_pac_status = 'retry'
        attachment = self.env['ir.attachment']
        for func in lambda_functions:
            order_filter = orders.filtered(func) 
            if not order_filter:
                continue
            cfdi_values = order_filter._l10n_mx_edi_create_cfdi(folio=self.name, periodo=self.periodicidad, mes=self.meses if self.periodicidad != '05' else self.bmeses, yearg=self.yearg)
            error = cfdi_values.pop('error', None)
            cfdi = cfdi_values.pop('cfdi', None)
            if error:
                self.message_post(body=error)
                signed.append(False)
                continue

            filename = self.name.replace("/","_") #order_filter.get_file_name()
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
            print("AAAAAAAAAAAAAAAAAAAAAAAA ",cfdi_values)
            if cfdi_values.get('errors'):
                self.message_post(body=cfdi_values.get('errors'))
            if cfdi_values:
                self._l10n_mx_edi_post_sign_process(cfdi_values, order_filter)
                signed.append(bool(cfdi_values.get('cfdi_signed', False)))
            orders = orders - order_filter
        if all(signed):
            self.l10n_mx_edi_pac_status = 'signed'
            for o in self.global_invoice_ids:
                o.pos_order_id.global_invoice_id = self.id
        try:
            self.l10n_mx_edi_update_sat_status()
        except Exception as e:
            print(e)

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
        company_id = self.company_id
        pac_name = company_id.l10n_mx_edi_pac
        if not pac_name:
            return False
        # Get the informations about the pac
        credentials = getattr(edi, '_l10n_mx_edi_get_values_%s_credentials' % pac_name)(self.company_id)
        if service_type == 'cancel':
            return getattr(edi, '_l10n_mx_edi_%s_%s_service' % (pac_name, service_type))(self.l10n_mx_edi_uuid, self.company_id, credentials, uuid_replace=None, motivo='04')
        else:
            return getattr(edi, '_l10n_mx_edi_%s_%s' % (pac_name, service_type))(False, credentials, cfdi)


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
        filename = self.name.replace("/","_") #order_ids.get_file_name()
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
            #report = self.env.ref('l10n_mx_edi_invoice_global.l10n_mx_edi_report_session')
            xml = objectify.fromstring(xml_signed)
            data = self._l10n_mx_edi_decode_cfdi(xml_signed)
            data.update({'cfdi': xml})
            # The generation of report does not work in test environment
            # because of this issue https://github.com/odoo/odoo/issues/18841
            # if not config['test_enable']:
            # pdf, ext = report._render_qweb_pdf(self.ids, data)
            # print("PDF: ",pdf, ext)
            # attachment_id = self.env[
            #     'ir.attachment'].with_context(ctx).create({
            #     'name': '%s.%s' % (filename, ext),
            #     'res_id': self.id,
            #     'res_model': self._name,
            #     'datas': base64.b64encode(pdf),
            #     'description': 'Printed representation of the CFDI',
            # })
            # attach.extend([attachment_id.id])
            uuid = self.l10n_mx_edi_get_tfd_etree(xml).get('UUID', '')
            self.l10n_mx_edi_uuid = uuid
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

    def _l10n_mx_edi_decode_cfdi(self, cfdi_data=None):
        ''' Helper to extract relevant data from the CFDI to be used, for example, when printing the invoice.
        :param cfdi_data:   The optional cfdi data.
        :return:            A python dictionary.
        '''
        self.ensure_one()

        def get_node(cfdi_node, attribute, namespaces):
            if hasattr(cfdi_node, 'Complemento'):
                node = cfdi_node.Complemento.xpath(attribute, namespaces=namespaces)
                return node[0] if node else None
            else:
                return None

        def get_cadena(cfdi_node, template):
            if cfdi_node is None:
                return None
            cadena_root = etree.parse(tools.file_open(template))
            return str(etree.XSLT(cadena_root)(cfdi_node))

        # Find a signed cfdi.
        if not cfdi_data:
            signed_edi = self._get_l10n_mx_edi_signed_edi_document()
            if signed_edi:
                cfdi_data = base64.decodebytes(signed_edi.attachment_id.with_context(bin_size=False).datas)

        # Nothing to decode.
        if not cfdi_data:
            return {}

        cfdi_node = fromstring(cfdi_data)
        tfd_node = get_node(
            cfdi_node,
            'tfd:TimbreFiscalDigital[1]',
            {'tfd': 'http://www.sat.gob.mx/TimbreFiscalDigital'},
        )

        return {
            'uuid': ({} if tfd_node is None else tfd_node).get('UUID'),
            'supplier_rfc': cfdi_node.Emisor.get('Rfc', cfdi_node.Emisor.get('rfc')),
            'customer_rfc': cfdi_node.Receptor.get('Rfc', cfdi_node.Receptor.get('rfc')),
            'amount_total': cfdi_node.get('Total', cfdi_node.get('total')),
            'cfdi_node': cfdi_node,
            'usage': cfdi_node.Receptor.get('UsoCFDI'),
            'payment_method': cfdi_node.get('formaDePago', cfdi_node.get('MetodoPago')),
            'bank_account': cfdi_node.get('NumCtaPago'),
            'sello': cfdi_node.get('sello', cfdi_node.get('Sello', 'No identificado')),
            'sello_sat': tfd_node is not None and tfd_node.get('selloSAT', tfd_node.get('SelloSAT', 'No identificado')),
            'cadena': tfd_node is not None and get_cadena(tfd_node, CFDI_XSLT_CADENA_TFD) or get_cadena(cfdi_node, CFDI_XSLT_CADENA),
            'certificate_number': cfdi_node.get('noCertificado', cfdi_node.get('NoCertificado')),
            'certificate_sat_number': tfd_node is not None and tfd_node.get('NoCertificadoSAT'),
            'expedition': cfdi_node.get('LugarExpedicion'),
            'fiscal_regime': cfdi_node.Emisor.get('RegimenFiscal', ''),
            'emission_date_str': cfdi_node.get('fecha', cfdi_node.get('Fecha', '')).replace('T', ' '),
            'stamp_date': tfd_node is not None and tfd_node.get('FechaTimbrado', '').replace('T', ' '),
        }

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
        cancelled = cfdi_values.get('success', '')
        code = cfdi_values.get('code', '')
        msg = cfdi_values.get('msg', '')
        filename = cfdi_values.get('filename', '')
        if cancelled:
            self.l10n_mx_edi_pac_status = 'cancelled'
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

    def l10n_mx_edi_cancel(self):
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
                olist = []
                for x in self.global_invoice_ids:
                    olist.append(x.pos_order_id.id)
                orders = self.env['pos.order'].search([("id", "in", olist)])
                func = (lambda r: r.partner_id) if _(
                    'with_partner') in att.name else (
                    lambda r: not r.partner_id)
                order_ids = orders.filtered(func)
                cfdi_values.update({'filename': att.name})
                self._l10n_mx_edi_post_cancel_process(
                    cfdi_values, order_ids, att)

                cancel.append(cfdi_values.get('cancelled', False))
                self.l10n_mx_edi_update_sat_status()
                for x in self.global_invoice_ids:
                    x.pos_order_id.global_invoice_id = False
                self.global_invoice_ids.unlink()
                for x in self.global_invoice_ids:
                    x.pos_order_id.global_invoice_id = False
                self.global_invoice_ids.unlink()

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
        print("FILENAME: ", filename)
        domain = [
            ('res_id', '=', self.id),
            ('res_model', '=', self._name),
            ('name', '=', filename)]
        return self.env['ir.attachment'].search(domain)

    @api.model
    def l10n_mx_edi_retrieve_last_attachment(self, filename):
        attachment_ids = self.l10n_mx_edi_retrieve_attachments(filename)
        return attachment_ids[0] if attachment_ids else None
    
    
    @api.model
    def l10n_mx_edi_get_gi_last_attachment(self):
        for rec in self:
            def get_node(cfdi_node, attribute, namespaces):
                if hasattr(cfdi_node, 'Complemento'):
                    node = cfdi_node.Complemento.xpath(attribute, namespaces=namespaces)
                    return node[0] if node else None
                else:
                    return None
            filename = "cancelled_"+rec.name.replace("/","_")+".xml" if rec.l10n_mx_edi_pac_status == "cancelled" else rec.name.replace("/","_")+".xml"
            attachment_ids = rec.l10n_mx_edi_retrieve_attachments(filename)
            attach = base64.decodebytes(attachment_ids[0].with_context(bin_size=False).datas) if attachment_ids else None
            cfdi_node = fromstring(attach)
            tfd_node = get_node(
                cfdi_node,
                    'tfd:TimbreFiscalDigital[1]',
                    {'tfd': 'http://www.sat.gob.mx/TimbreFiscalDigital'},
                )
            print("FILE: ", cfdi_node)
            return cfdi_node if attachment_ids else None
    

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
        headers = {'SOAPAction': 'http://tempuri.org/IConsultaCFDIService/Consulta',
                   'Content-Type': 'text/xml; charset=utf-8'}  # noqa
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
    
    
class GlobalInvoiceLine(models.Model):
    _name = "global.invoice.line"

    global_invoice_id = fields.Many2one(
        comodel_name='global.invoice',
        string='Global Invoice',
        required=False)
    pos_order_id = fields.Many2one(
        comodel_name='pos.order',
        string='Pos Order',
        required=False)
    pos_reference = fields.Char(string="Recibo",
                                related="pos_order_id.pos_reference",
                                readonly=True)
    session_id = fields.Many2one("pos.session",
                                     string="Sesion",
                                     related="pos_order_id.session_id",
                                     readonly=True)
    res_partner = fields.Many2one(
        comodel_name='res.partner',
        string='Cliente',
        related="pos_order_id.partner_id",
        readonly=True)
    date_order = fields.Datetime(string="Fecha",
                                 related="pos_order_id.date_order",
                                 readonly=True)
    amount_tax = fields.Float(string="Impuestos",
                                 related="pos_order_id.amount_tax",
                                 readonly=True)
    amount_total = fields.Float(string="Total",
                                   related="pos_order_id.amount_total",
                                   readonly=True)
    currency_id = fields.Many2one("res.currency",
                                  string="Moneda",
                                  related="pos_order_id.currency_id",
                                  readonly=True)

