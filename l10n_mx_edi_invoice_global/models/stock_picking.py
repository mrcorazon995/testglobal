import re
from odoo.exceptions import ValidationError
from odoo import api, models


class StockPicking(models.Model):
    _inherit = "stock.picking"

    def l10n_mx_edi_action_send_delivery_guide(self):
        if self.l10n_mx_edi_transport_type == '01':
            self._validate_carta_porte()
        res = super().l10n_mx_edi_action_send_delivery_guide()
        return res

    def _validate_carta_porte(self):
        errores = []
        msg = 'Tiene activada la Validacion de Carta Porte estos son los errores encontrados \n\n'
        name_numbers = list(re.finditer('\d+', self.name))
        mx_tz = self.env['account.move']._l10n_mx_edi_get_cfdi_partner_timezone(self.picking_type_id.warehouse_id.partner_id or self.company_id.partner_id)
        date_fmt = '%Y-%m-%dT%H:%M:%S'
        warehouse_zip = self.picking_type_id.warehouse_id.partner_id and self.picking_type_id.warehouse_id.partner_id.zip or self.company_id.zip
        origin_type, origin_uuids = None, []
        self._get_errors_partners(errores)
        if not self.company_id.partner_id:
            msg = msg+ ''
        if self.l10n_mx_edi_origin and '|' in self.l10n_mx_edi_origin:
            split_origin = self.l10n_mx_edi_origin.split('|')
            if len(split_origin) == 2:
                origin_type = split_origin[0]
                origin_uuids = split_origin[1].split(',')
        cfdi_date = self.date_done.astimezone(mx_tz).strftime(date_fmt)
        scheduled_date = self.scheduled_date.astimezone(mx_tz).strftime(date_fmt)
        folio_number = name_numbers[-1].group()
        if not folio_number:
            errores.append('No hay numero de folio\n') 
        origin_type = origin_type
        origin_uuids = origin_uuids
        serie = re.sub('\W+', '', self.name[:name_numbers[-1].start()])
        if not serie:
            errores.append('No hay numero de serie\n')
        lugar_expedicion = warehouse_zip
        if not lugar_expedicion:
            errores.append('No hay lugar de expedicion\n')
        supplier = self.company_id
        customer = self.partner_id.commercial_partner_id
        moves = self.move_lines.filtered(lambda ml: ml.quantity_done > 0)
        weight_uom = self.env['product.template']._get_weight_uom_id_from_ir_config_parameter()
        if not weight_uom:
            errores.append('No hay lugar de Medida de Peso\n')
        if errores:
            for rec in errores:
                msg = msg + rec
            raise ValidationError(msg)

    def _get_errors_partners(self, errores):
        # supplier
        partner_company = self.company_id.partner_id
        # Customer
        partner_customer = self.partner_id
        if not partner_company.street_name:
            errores.append('No hay una calle definida en ' + partner_company.name + '\n')
        if not partner_customer.street_name:
            errores.append('No hay una calle definida en ' + partner_customer.name + '\n')
        if not partner_company.city_id and partner_company.state_id and partner_company.country_id:
            errores.append(
                ('Revisar los campos de Ciudad Estado y Pais en '
                    + partner_company.name +' porque falta alguno de configurar' 
                    + '\n')
                )
        if not partner_customer.city_id and partner_customer.state_id and partner_customer.country_id:
            errores.append(
                ('Revisar los campos de Ciudad Estado y Pais en '
                    + partner_customer.name +' porque falta alguno de configurar' 
                    + '\n')
                )
        return errores