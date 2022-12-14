# Part of Odoo. See LICENSE file for full copyright and licensing details.

from odoo import api, models


class AccountMove(models.Model):
    _inherit = "account.move"

    @api.onchange('partner_id', 'company_id')
    def _onchange_partner_id(self):
        """Set payment method and usage"""
        res = super()._onchange_partner_id()
        if self.env.context.get('force_payment_method'):
            self.l10n_mx_edi_payment_method_id = self.env.context.get(
                'force_payment_method')
        return res
