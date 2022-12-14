from odoo import fields, models


class PosPaymentMethod(models.Model):
    _inherit = "pos.payment.method"

    l10n_mx_edi_payment_method_id = fields.Many2one(
        'l10n_mx_edi.payment.method',
        string='Payment Way',
        default=lambda self: self.env.ref('l10n_mx_edi.payment_method_otros',
                                          raise_if_not_found=False),
        help='Indicates the way the pos order was/will be paid, where the '
             'options could be: Cash, Nominal Check, Credit Card, etc. '
             'Leave empty if unkown and the XML will show "Unidentified".',
    )
