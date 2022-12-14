# -*- coding: utf-8 -*-

from odoo import models, fields, api, _
from odoo.exceptions import UserError, ValidationError
from odoo.tools.float_utils import float_repr
from suds.client import Client
import logging
import base64
from bs4 import BeautifulSoup
import dateutil

logging.basicConfig(level=logging.INFO)
logging.getLogger('suds.client').setLevel(logging.DEBUG)
logging.getLogger('suds.transport').setLevel(logging.DEBUG)
logging.getLogger('suds.xsd.schema').setLevel(logging.DEBUG)

class AccountMove(models.Model):
    _inherit = 'account.move'
    """Inherit to account move"""

    edi_uuid_cancelled = fields.Char(string="UUID Cancelado", copy=False)
    l10n_mx_edi_cfdi_supplier_rfc_cancelled =  fields.Char(string="RFC supplier cancelled", copy=False)
    edi_motivo_cancel = fields.Selection(
        string='Motivo de cancelacion',
        selection=[('02', '02-Comprobantes emitidos con errores sin relación'),
                   ('03', '03-No se llevó a cabo la operación'),
                   ('04', '04-Operación nominativa relacionada en una factura global'),],
        required=False, )



    def get_acuse_values(self):
        for record in self:
            attachment = record.acuse_retrieve_attachment()
            print("ATTACHMENT:    ", attachment)
            bs_data = BeautifulSoup(attachment._file_read(attachment.store_fname), 'xml')
            

            datas = attachment._file_read(attachment.store_fname) if attachment else None
            if not datas:
                return
            byte_datas = base64.decodebytes(datas)
            # soup = BeautifulSoup(byte_datas,'xml')
            a = bs_data.find('UUID')
            print("TEST |||||||| ",a.text)
            vals = bs_data.find('CancelaCFDResponse')
            uuid = bs_data.find('UUID')
            vals['uuid'] = uuid.text
            sello = bs_data.find('Modulus')
            vals['sello'] = sello.text
            cancela_data = bs_data.find_all('CancelaCFDResult')
            fecha = ""
            rfcemisor = ""
            for x in cancela_data:
            	fecha = x.get("Fecha")
            	rfcemisor = x.get("RfcEmisor")
            f = dateutil.parser.parse(fecha)
            vals['Fecha'] = f.strftime("%d/%m/%Y, %H:%M:%S")
            vals['RfcEmisor'] = rfcemisor
            print("VALUES: ", fecha, vals)
            return vals

    @api.model
    def acuse_retrieve_attachment(self):
        self.ensure_one()
        print("NAME: ", self.name)
        inv_name = self.name.replace("/","_")
        # inv_name.replace("/","-")
        name ='acuse%s.xml' % inv_name
        domain = [
            ('res_id', '=', self.id),
            ('res_model', '=', self._name),
            ('name', '=', name)]
        acuse = self.env['ir.attachment'].search(domain)
        print("ACUSE FILE: ", acuse, inv_name)
        return acuse #self.env['ir.attachment'].search(domain)

    def button_cancel_04(self):
        order = self.env['sale.order'].search([('name','=',self.invoice_origin)], limit=1)
        """
        if not self.invoice_origin:
            raise ValidationError(_('El campo (Origen) esta vacio'))

        if not order:
            raise ValidationError(_('No se encontro la orden de venta (%s)') % self.invoice_origin)
        """

        if not self.l10n_mx_edi_cfdi_uuid:
            raise ValidationError(_('El campo (Folio Fiscal) esta vacio'))

        lines = []
        invoice_vals = False
        if order:
            for l in self.invoice_line_ids:
                lines.append(
                        {
                            'name': l.name,
                            'price_unit': l.price_unit,
                            'quantity': l.quantity,
                            'product_id': l.product_id.id,
                            'product_uom_id': l.product_uom_id.id,
                            'tax_ids': [(6, 0, l.tax_ids.ids)],
                            'sale_line_ids': [(6, 0, l.sale_line_ids.ids)],
                            'analytic_tag_ids': [(6, 0, l.analytic_tag_ids.ids)],
                            'analytic_account_id': order.analytic_account_id.id or False,
                        }
                    )
            invoice_vals = {
                'ref': order.client_order_ref,
                'move_type': 'out_invoice',
                'invoice_origin': order.name,
                'invoice_user_id': order.user_id.id,
                'narration': order.note,
                'partner_id': order.partner_invoice_id.id,
                'fiscal_position_id': (order.fiscal_position_id or order.fiscal_position_id.get_fiscal_position(
                    order.partner_id.id)).id,
                'partner_shipping_id': order.partner_shipping_id.id,
                'currency_id': order.pricelist_id.currency_id.id,
                'payment_reference': order.reference,
                'invoice_payment_term_id': order.payment_term_id.id,
                'partner_bank_id': order.company_id.partner_id.bank_ids[:1].id,
                'team_id': order.team_id.id,
                'campaign_id': order.campaign_id.id,
                'medium_id': order.medium_id.id,
                'source_id': order.source_id.id,
                'invoice_line_ids': lines,
                'l10n_mx_edi_origin': "04|%s" % self.l10n_mx_edi_cfdi_uuid
            }
        else:
            for l in self.invoice_line_ids:
                lines.append(
                    {
                        'name': l.name,
                        'price_unit': l.price_unit,
                        'quantity': l.quantity,
                        'product_id': l.product_id.id,
                        'product_uom_id': l.product_uom_id.id,
                        'tax_ids': [(6, 0, l.tax_ids.ids)],
                        'sale_line_ids': [],
                        'analytic_tag_ids': [(6, 0, l.analytic_tag_ids.ids)],
                        'analytic_account_id': False,
                    }
                )

            invoice_vals = {
                'ref': self.ref,
                'move_type': 'out_invoice',
                'invoice_origin': self.invoice_origin,
                'invoice_user_id': self.invoice_user_id,
                'narration': self.narration,
                'partner_id': self.partner_id.id,
                'fiscal_position_id': self.fiscal_position_id.id,
                'partner_shipping_id': self.partner_shipping_id.id,
                'currency_id': self.currency_id.id,
                'payment_reference': self.payment_reference,
                'invoice_payment_term_id': self.invoice_payment_term_id.id,
                'partner_bank_id': self.partner_bank_id.id,
                'team_id': self.team_id.id,
                'campaign_id': self.campaign_id.id,
                'medium_id': self.medium_id.id,
                'source_id': self.source_id.id,
                'invoice_line_ids': lines,
                'l10n_mx_edi_origin': "04|%s" % self.l10n_mx_edi_cfdi_uuid
            }

        self.env['account.move'].create(invoice_vals)


    def button_cancel_posted_moves(self):
        for rec in self:
            res = super(AccountMove, self).button_cancel_posted_moves()
            if rec.l10n_mx_edi_cfdi_uuid:
                rec.edi_uuid_cancelled = rec.l10n_mx_edi_cfdi_uuid
                rec.l10n_mx_edi_cfdi_supplier_rfc_cancelled = rec.l10n_mx_edi_cfdi_supplier_rfc
            return res


    def l10n_mx_edi_update_sat_status(self):
        '''Synchronize both systems: Odoo & SAT to make sure the invoice is valid.
        '''
        for move in self:
            supplier_rfc = move.l10n_mx_edi_cfdi_supplier_rfc
            customer_rfc = move.l10n_mx_edi_cfdi_customer_rfc
            total = float_repr(move.l10n_mx_edi_cfdi_amount, precision_digits=move.currency_id.decimal_places)
            uuid = move.l10n_mx_edi_cfdi_uuid or move.edi_uuid_cancelled
            try:
                status = self.env['account.edi.format']._l10n_mx_edi_get_sat_status(supplier_rfc, customer_rfc, total, uuid)
            except Exception as e:
                move.message_post(body=_("Failure during update of the SAT status: %(msg)s", msg=str(e)))
                continue

            if status == 'Vigente':
                move.l10n_mx_edi_sat_status = 'valid'
            elif status == 'Cancelado':
                move.l10n_mx_edi_sat_status = 'cancelled'
            elif status == 'No Encontrado':
                move.l10n_mx_edi_sat_status = 'not_found'
            else:
                move.l10n_mx_edi_sat_status = 'none'

    def get_receipt_cancel(self):
        for rec in self:
            username = False
            password = False
            sign_url = False
            cancel_url = False
            if rec.company_id.l10n_mx_edi_pac_test_env:
                username = 'cfdi@vauxoo.com'
                password = 'vAux00__'
                sign_url = 'http://demo-facturacion.finkok.com/servicios/soap/stamp.wsdl'
                cancel_url = "http://demo-facturacion.finkok.com/servicios/soap/cancel.wsdl"
             
            else:
                if not rec.company_id.l10n_mx_edi_pac_username or not rec.company_id.l10n_mx_edi_pac_password:
                    message = self.env['mail.message'].create({
                        'model': self._name,
                        'res_id': self.id,
                        'body': "Nombre de usuario y/o contraseña no encontrados",
                        'message_type': "notification",
                        'subtype_id': 2,
                        'author_id': self.env.user.partner_id.id,
                    })

                else:
                    username = rec.company_id.l10n_mx_edi_pac_username
                    password = rec.company_id.l10n_mx_edi_pac_password
                    sign_url = 'http://facturacion.finkok.com/servicios/soap/stamp.wsdl'
                    cancel_url = "http://facturacion.finkok.com/servicios/soap/cancel.wsdl"
                

            # username = # Usuario de Finkok
            # password = # Contraseña de Finkok
            taxpayer_id = rec.l10n_mx_edi_cfdi_supplier_rfc_cancelled # RFC emisor
            uuid = rec.edi_uuid_cancelled # UUID
            tipo = 'C' # Tipo de acuse a recibir ("R" para recepción o "C" para cancelación), por default es de cancelación.
             
            # Consumir el método Get_receipt del web service de Cancelación
            # url = "https://facturacion.finkok.com/servicios/soap/cancel.wsdl"
            client = Client(cancel_url,cache=None)
             
            result = client.service.get_receipt(username, password, taxpayer_id, uuid, tipo)
            # receipt = result.receipt
            try:
                # print("|||||||||||||||||||||| RECIBO ||||||||||||||||||||\n", type(result), result, result.receipt)
                arr = bytes(result.receipt, 'utf-8')
                attach = self.env['ir.attachment'].create({
                    'name': "acuse%s.xml" % self.name.replace("/","_"),
                    'type': 'binary',
                    'res_id': self.id,
                    'res_model': 'account.move',
                    'datas': base64.b64encode(arr),
                    'mimetype': 'application/x-pdf'
                })
                message = self.env['mail.message'].create({
                    'model': self._name,
                    'res_id': self.id,
                    'body': "Acuse generado con exito.",
                    'message_type': "notification",
                    'subtype_id': 2,
                    'author_id': self.env.user.partner_id.id,
                    'attachment_ids': attach
                })
            except Exception as e:
                message = self.env['mail.message'].create({
                    'model': self._name,
                    'res_id': self.id,
                    'body': "ERROR: %s" % result.error,
                    'message_type': "notification",
                    'subtype_id': 2,
                    'author_id': self.env.user.partner_id.id,
                })
                # print("|||||||||||||||||||||| ERROR ||||||||||||||||||||\n", type(result), result, result.error)
             
            # # Generación del archivo con el xml
            # archivo = open("Get_receipt.xml","w")
            # archivo.write(str(receipt))
            # archivo.close()
