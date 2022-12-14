# Part of Odoo. See LICENSE file for full copyright and licensing details.

import unittest
import base64
from lxml.objectify import fromstring

from odoo import fields
from odoo.tests.common import TransactionCase


class PointOfSale(TransactionCase):

    def setUp(self):
        super(PointOfSale, self).setUp()
        self.pos_order_obj = self.env['pos.order']
        self.pmp_obj = self.env['pos.make.payment']
        self.att_obj = self.env['ir.attachment']
        self.payment = self.env['account.payment.register']
        self.env.user.groups_id |= self.env.ref(
            'point_of_sale.group_pos_manager') | self.env.ref(
                'account.group_account_invoice')
        # self.env.user.company_ids = [(4, self.env.ref('base.main_company').id)]
        # Partner with RFC
        self.partner1 = self.env.ref('base.res_partner_12')
        self.partner1.commercial_partner_id.vat = 'VCO120608KR7'
        # Partner without RFC
        self.partner2 = self.env.ref('base.res_partner_address_2')
        self.partner2.commercial_partner_id.vat = False
        self.partner2.commercial_partner_id.l10n_mx_edi_usage = 'P01'

        self.product = self.env.ref('product.product_product_8')
        self.product.unspsc_code_id = self.ref('product_unspsc.unspsc_code_01010101')
        self.tax_16 = self.env['account.tax'].create({
            'name': 'tax_16',
            'amount_type': 'percent',
            'amount': 16,
            'type_tax_use': 'sale',
            'l10n_mx_tax_type': 'Tasa',
        })
        self.tax_10_negative = self.env['account.tax'].create({
            'name': 'tax_10_negative',
            'amount_type': 'percent',
            'amount': -10,
            'type_tax_use': 'sale',
            'l10n_mx_tax_type': 'Tasa',
        })
        self.company = self.env.user.company_id
        account = self.env['account.account'].search([
            ('code', '=', '208.01.01'), ('company_id', '=', self.company.id)], limit=1)
        account_refund = self.env['account.account'].search([
            ('code', '=', '209.01.01'), ('company_id', '=', self.company.id)], limit=1)
        tag_iva = self.env.ref('l10n_mx.tag_iva')
        # Set account_id in the generated repartition lines. Automatically, nothing is set.
        (self.tax_16 | self.tax_10_negative).mapped('invoice_repartition_line_ids').account_id = account
        (self.tax_16 | self.tax_10_negative).mapped('invoice_repartition_line_ids').tag_ids = tag_iva
        (self.tax_16 | self.tax_10_negative).mapped('refund_repartition_line_ids').account_id = account_refund
        (self.tax_16 | self.tax_10_negative).mapped('refund_repartition_line_ids').tag_ids = tag_iva

    def test_001_l10n_mx_edi_pos(self):
        """Test all cases of the point of sale"""
        # Create order with all data to invoice.
        # In this case, the order created have all information to generate the
        # invoice, but is not generated from PoS.
        # Verify that the order is invoiced with the Odoo process, the session
        # must not have XML attached.
        self.session = self.create_session()
        self.create_order(self.partner1.id)
        self.session.action_pos_session_closing_control()
        self.assertEqual(self.session.l10n_mx_edi_pac_status, "signed",
                         self.session.message_ids.mapped('body'))
        self.assertFalse(
            self.att_obj.search([
                ('res_model', '=', 'pos.session'),
                ('res_id', '=', self.session.id)]), 'Attachments generated')
        # Verify that invoice is generated automatically when is created the
        # order, without generate the invoice from PoS.
        order = self.session.order_ids
        self.assertTrue(
            order.account_move, 'Invoice not validated')

        # Generated order with partner, but address is incomplete.
        # Is created the order with partner, but the partner have not RFC.
        # The session must have one XML attached and one PDF
        self.session = self.create_session()
        self.create_order(self.partner2.id)
        self.session.action_pos_session_closing_control()
        self.assertEqual(self.session.l10n_mx_edi_pac_status, "signed",
                         self.session.message_ids.mapped('body'))
        self.assertEqual(
            len(self.att_obj.search([
                ('res_model', '=', 'pos.session'),
                ('res_id', '=', self.session.id)]).ids), 1,
            'Attachments not generated')
        self.session.l10n_mx_edi_update_sat_status()
        self.assertEqual(self.session.l10n_mx_edi_sat_status, 'not_found', self.session.l10n_mx_edi_sat_status)

        # Generated the order without partner.
        # The session must have one XML attached and one PDF
        self.session = self.create_session()
        self.create_order()
        self.company.l10n_mx_edi_pac = False
        self.session.action_pos_session_close()
        # Try send to stamp the XML without PAC configured.
        self.assertEqual(self.session.l10n_mx_edi_pac_status, "retry",
                         self.session.message_ids.mapped('body'))
        self.company.l10n_mx_edi_pac = 'finkok'
        self.session.l10n_mx_edi_update_pac_status()
        self.assertEqual(self.session.l10n_mx_edi_pac_status, "signed",
                         self.session.message_ids.mapped('body'))
        self.assertEqual(
            len(self.att_obj.search([
                ('res_model', '=', 'pos.session'),
                ('res_id', '=', self.session.id)]).ids), 1,
            'Attachments not generated')
        self.session.l10n_mx_edi_update_sat_status()
        self.assertEqual(self.session.l10n_mx_edi_sat_status, 'not_found', self.session.l10n_mx_edi_sat_status)

        # Generated two orders without partner, with partner and without
        # complete address.
        # The session must generate only one attachment by case.
        self.session = self.create_session()
        self.create_order()
        self.create_order(self.partner1.id)
        self.create_order(self.partner2.id)
        self.create_order()
        self.create_order(self.partner1.id)
        self.create_order(self.partner2.id)
        self.session.action_pos_session_closing_control()
        self.assertEqual(self.session.l10n_mx_edi_pac_status, "signed",
                         self.session.message_ids.mapped('body'))
        self.assertEqual(
            len(self.att_obj.search([
                ('res_model', '=', 'pos.session'),
                ('res_id', '=', self.session.id)]).ids), 2,
            'Attachments not generated')
        self.session.l10n_mx_edi_update_sat_status()
        self.assertEqual(self.session.l10n_mx_edi_sat_status, 'not_found', self.session.l10n_mx_edi_sat_status)
        # Call method that cancel XML, the XML is not cancelled by the time
        # between the stamp and cancel.
        self.session.l10n_mx_edi_cancel()
        self.assertTrue(
            self.session.l10n_mx_edi_pac_status in ['cancelled', 'to_cancel'],
            self.session.message_ids.mapped('body'))

        # Generate an order with include base amount
        self.tax_16.write({'include_base_amount': True})
        self.session = self.create_session()
        order = self.create_order(tax_included=True)
        self.session.action_pos_session_closing_control()
        self.assertEqual(self.session.l10n_mx_edi_pac_status, "signed",
                         self.session.message_ids.mapped('body'))
        attach = self.att_obj.search([
            ('res_model', '=', 'pos.session'),
            ('res_id', '=', self.session.id)]).datas
        attach = fromstring(base64.b64decode(attach))
        xml_total = attach.get('Total')
        self.assertEqual(round(order.amount_total, 2), float(xml_total),
                         'The amount with include base amount is incorrect')

    def test_002_create_credit_note_previous_open_invoice(self):
        """Create a credit note from a returned order with previous invoice
        validated in open state
        """
        self.session = self.create_session()
        order = self.create_order(self.partner2.id)
        # Creating Invoice
        order.action_pos_order_invoice()
        order.account_move.action_post()
        # Validating that the invoice state is open
        self.assertEqual(order.account_move.state, 'posted', 'The invoice related to the order is not posted')
        # Creating Refund
        refund = order.refund()
        # Validating if the refund was created
        self.assertTrue(refund.get('res_id'), 'The refund was not created')
        # Creating the object for the return
        refund = self.pos_order_obj.browse(refund.get('res_id'))
        # Creating payment
        payment = self.pmp_obj.with_context(active_id=order.id).create({
            'payment_method_id': order.session_id.config_id.payment_method_ids[0].id,
            'amount': order.amount_total
        })
        payment.check()
        # Creating the credit note
        refund.action_pos_order_invoice()
        # Validate if the credit note was created and its state
        self.assertEqual((refund.account_move.state, refund.account_move.move_type),
                         ('posted', 'out_refund'), 'The credit note was not created')
        self.session.action_pos_session_closing_control()
        # Validating that the original invoice is paid too
        self.assertEqual(order.account_move.payment_state, 'paid', 'The invoice related to the order is not paid')
        # Validating the state of the refund
        self.assertEqual(refund.state, 'invoiced', 'The state of the order was not updated')

    def test_003_create_credit_note(self):
        """Create a credit note from a returned order without previous invoice
        """
        self.session = self.create_session()
        order = self.create_order(self.partner2.id)

        # Creating Refund
        refund = order.refund()
        # Validating if the refund was created
        self.assertTrue(refund.get('res_id'),
                        'The refund was not created')
        # Creating the object for the return
        refund = self.pos_order_obj.browse(refund.get('res_id'))
        # Creating payment
        payment = self.pmp_obj.with_context(active_id=order.id).create({
            'payment_method_id': order.session_id.config_id.payment_method_ids[0].id,  # noqa
            'amount': order.amount_total
        })
        payment.check()
        # Creating the credit note
        refund.action_pos_order_invoice()
        # Validate if the credit note was created and its state
        self.assertEqual((refund.account_move.state, refund.account_move.move_type),
                         ('posted', 'out_refund'),
                         'The credit note was not created')
        # Validating the state of the refund
        self.assertEqual(refund.state, 'invoiced',
                         'The state of the order was not updated')

    def test_004_refund_closed_session(self):
        """Create a refund from a closed session"""
        # create one order for case
        self.session = self.create_session()
        order = self.create_order()  # without partner
        self.create_order(self.partner1.id)  # with partner
        self.create_order(self.partner2.id)  # invoiced
        # close session
        self.session.action_pos_session_closing_control()
        self.assertEqual(self.session.state, 'closed',
                         'The session 1 is not closed')
        self.assertEqual(self.session.l10n_mx_edi_pac_status, "signed",
                         self.session.message_ids.mapped('body'))
        # create session 2
        session2 = self.create_session()
        # create refund
        refund = order.refund()
        # Validating if the refund was created
        self.assertTrue(refund.get('res_id'),
                        'The refund was not created')
        refund = self.pos_order_obj.browse(refund.get('res_id'))
        # Creating payment
        payment = self.pmp_obj.with_context(active_id=refund.id).create({
            'payment_method_id': refund.session_id.config_id.payment_method_ids[0].id,  # noqa
            'amount': refund.amount_total
        })
        payment.check()
        # Validating refund state
        self.assertEqual(refund.state, 'paid',
                         'The refund order was not paid')
        # close session 2
        session2.action_pos_session_closing_control()
        self.assertEqual(session2.state, 'closed', 'The session is not closed')
        self.assertEqual(session2.l10n_mx_edi_pac_status, "signed",
                         session2.message_ids.mapped('body'))

        filename = session2.order_ids.get_file_name()
        attachment = session2.l10n_mx_edi_retrieve_last_attachment(
            '%s.xml' % filename)
        self.assertTrue(attachment, 'attachment not created')
        xml = fromstring(base64.b64decode(attachment.datas))
        self.assertEqual(xml.get('TipoDeComprobante'), 'E',
                         "The refund is not set as an outcome")

    def test_005_no_consider_refund_without_cfdi_origin(self):
        """Don't consider refund without cfdi related"""
        self.session = self.create_session()
        order = self.create_order()
        company = order.company_id
        vat = company.vat
        company.vat = False
        self.session.action_pos_session_closing_control()
        self.assertEqual(self.session.l10n_mx_edi_pac_status, "retry",
                         self.session.message_ids.mapped('body'))
        company.vat = vat
        session2 = self.create_session()
        refund = order.refund()
        self.assertTrue(refund.get('res_id'), 'The refund was not created')
        refund = self.pos_order_obj.browse(refund.get('res_id'))
        payment = self.pmp_obj.with_context(active_id=refund.id).create({
            'payment_method_id': refund.session_id.config_id.payment_method_ids[0].id,  # noqa
            'amount': refund.amount_total
        })
        payment.check()
        self.assertEqual(refund.state, 'paid', 'The refund order was not paid')
        session2.action_pos_session_closing_control()
        self.assertEqual(session2.l10n_mx_edi_pac_status, "signed",
                         session2.message_ids.mapped('body'))
        attachments = self.att_obj.search([('res_model', '=', 'pos.session'),
                                           ('res_id', '=', session2.id)])
        self.assertFalse(attachments, 'attachment created')
        self.session.l10n_mx_edi_update_pac_status()
        self.assertEqual(self.session.l10n_mx_edi_pac_status, "signed",
                         self.session.message_ids.mapped('body'))
        attachments = self.att_obj.search([('res_model', '=', 'pos.session'),
                                           ('res_id', '=', self.session.id)])
        self.assertFalse(attachments, 'attachment created')

    def test_006_amount_line(self):
        """Order with negative amount in a line."""
        self.session = self.create_session()
        order = self.create_order(autopaid=False)
        order.write({'lines': [(0, 0, {
            'product_id': self.product.id,
            'price_unit': -50.0,
            'qty': 1.0,
            'price_subtotal': -50.0,
            'price_subtotal_incl': -50,
        })]})
        order._onchange_amount_all()
        self.pay_order(order)
        self.session.action_pos_session_closing_control()
        self.assertEqual(self.session.l10n_mx_edi_pac_status, "signed",
                         self.session.message_ids.mapped('body'))
        attach = self.att_obj.search([
            ('res_model', '=', 'pos.session'),
            ('res_id', '=', self.session.id)]).datas
        attach = fromstring(base64.b64decode(attach))
        xml_total = attach.get('Total')
        self.assertEqual(round(order.amount_total, 2), float(xml_total),
                         'The amount with include base amount is incorrect')
        attach = self.att_obj.search([
            ('res_model', '=', 'pos.session'),
            ('res_id', '=', self.session.id)]).datas
        attach = fromstring(base64.b64decode(attach))
        xml_total = attach.get('Total')
        self.assertEqual(round(order.amount_total, 2), float(xml_total),
                         'The amount with include base amount is incorrect')

    @unittest.skip("We can't sign CFDI 3.3 with SolucionFactible")
    def test_l10n_mx_edi_invoice_basic_sf(self):
        self.account_settings.create({'l10n_mx_edi_pac': 'solfact'}).execute()
        self.test_001_l10n_mx_edi_pos()
        self.test_005_refund_closed_session()

    def test_tax_include(self):
        # Generate an order with include base amount
        self.session = self.create_session()
        self.tax_16.include_base_amount = True
        self.product.taxes_id = [(6, 0, self.tax_16.ids)]
        order = self.create_order(autopaid=False)
        order.lines.unlink()
        order.write({
            'lines': [(0, 0, {
                'product_id': self.product.id,
                'price_unit': 21.2,
                'qty': 1.0,
                'price_subtotal': 18.28,
                'price_subtotal_incl': 21.20,
            }), (0, 0, {
                'product_id': self.product.id,
                'price_unit': 23.32,
                'qty': 2.0,
                'price_subtotal': 40.21,
                'price_subtotal_incl': 46.64,
            }), (0, 0, {
                'product_id': self.product.id,
                'price_unit': 24.38,
                'qty': 1.0,
                'price_subtotal': 21.02,
                'price_subtotal_incl': 24.38,
            })],
        })
        order._onchange_amount_all()
        self.pay_order(order)
        order.recompute()
        self.session.action_pos_session_closing_control()
        self.assertEqual(self.session.l10n_mx_edi_pac_status, "signed",
                         self.session.message_ids.mapped('body'))
        attach = self.att_obj.search([
            ('res_model', '=', 'pos.session'),
            ('res_id', '=', self.session.id)]).datas
        attach = fromstring(base64.b64decode(attach))
        xml_total = attach.get('Total')
        self.assertEqual(round(order.amount_total, 2), float(xml_total),
                         'The amount with include base amount is incorrect')

        # Generate an order with price include and discount
        self.session = self.create_session()
        self.tax_16.include_base_amount = True
        self.product.taxes_id = [(6, 0, self.tax_16.ids)]
        order = self.create_order(autopaid=False)
        order.lines.unlink()
        order.write({
            'lines': [(0, 0, {
                'product_id': self.product.id,
                'price_unit': 35.0,
                'discount': 10.0,
                'qty': 1.0,
                'price_subtotal': 31.50,
                'price_subtotal_incl': 33.39,
            })]
        })
        order._onchange_amount_all()
        self.pay_order(order)
        order.recompute()
        self.session.action_pos_session_closing_control()
        self.assertEqual(self.session.l10n_mx_edi_pac_status, "signed",
                         self.session.message_ids.mapped('body'))
        attach = self.att_obj.search([
            ('res_model', '=', 'pos.session'),
            ('res_id', '=', self.session.id)]).datas
        attach = fromstring(base64.b64decode(attach))
        xml_total = attach.get('Total')
        self.assertEqual(round(order.amount_total, 2), float(xml_total),
                         'The amount with price include is incorrect')

    def test_both_taxes0(self):
        """Test a PoS Order with IEPS 0% and IVA 0%"""
        tax0 = self.env['account.tax'].create({
            'name': 'IVA 0% PoS',
            'description': 'IVA(0%)',
            'amount': 0,
            'amount_type': 'percent',
            'type_tax_use': 'sale',
            'l10n_mx_tax_type': 'Tasa',
        })
        ieps0 = self.env['account.tax'].create({
            'name': 'IEPS 0% PoS',
            'description': 'IEPS(0%)',
            'amount': 0,
            'amount_type': 'percent',
            'type_tax_use': 'sale',
            'l10n_mx_tax_type': 'Tasa',
        })
        ieps_tag = self.env['account.account.tag'].search([('name', '=', 'IEPS')])
        iva_tag = self.env['account.account.tag'].search([('name', '=', 'IVA')])
        for rep_line in ieps0.invoice_repartition_line_ids:
            rep_line.tag_ids |= ieps_tag
        for rep_line in tax0.invoice_repartition_line_ids:
            rep_line.tag_ids |= iva_tag
        self.product.taxes_id = [(6, 0, [tax0.id, ieps0.id])]
        self.session = self.create_session()
        order = self.create_order(autopaid=False)
        order.lines.unlink()
        order.write({'lines': [(0, 0, {
            'product_id': self.product.id,
            'price_unit': 100.0,
            'qty': 1.0,
            'tax_ids': [(6, 0, [tax0.id, ieps0.id])],
            'price_subtotal': 100.0,
            'price_subtotal_incl': 100,
        })]})
        order._onchange_amount_all()
        self.pay_order(order)
        order.recompute()
        self.session.action_pos_session_closing_control()
        self.assertEqual(self.session.l10n_mx_edi_pac_status, "signed",
                         self.session.message_ids.mapped('body'))
        attach = self.att_obj.search([
            ('res_model', '=', 'pos.session'),
            ('res_id', '=', self.session.id)]).datas
        attach = fromstring(base64.b64decode(attach))
        self.assertEqual(len(attach.Conceptos.Concepto.Impuestos.Traslados.Traslado), 2,
                         'Missing taxes on concept')

    def test_partial_refund(self):
        """Ensure that if have a partial refund, the orders are not skipped"""
        self.session = self.create_session()
        order = self.create_order()
        order.write({'lines': [(0, 0, {
            'product_id': self.product.id,
            'price_unit': 100.0,
            'qty': 1.0,
            'price_subtotal': 100.0,
            'price_subtotal_incl': 100,
        })]})
        order._onchange_amount_all()
        refund = order.refund()
        refund = self.pos_order_obj.browse(refund.get('res_id'))
        refund.lines[-1].unlink()
        refund._onchange_amount_all()
        payment = self.pmp_obj.with_context(active_id=refund.id).create({
            'payment_method_id': refund.session_id.config_id.payment_method_ids[0].id,
            'amount': refund.amount_total,
        })
        payment.check()
        self.session.action_pos_session_closing_control()
        self.assertEqual(self.session.l10n_mx_edi_pac_status, "signed",
                         self.session.message_ids.mapped('body'))
        self.assertEqual(
            len(self.att_obj.search([('res_model', '=', 'pos.session'), ('res_id', '=', self.session.id)]).ids), 2,
            'Attachments not generated')

    def test_refund_in0(self):
        """Ensure that if refund have not lines, the original orden is not skipped"""
        self.session = self.create_session()
        order = self.create_order()
        refund = order.refund()
        refund = self.pos_order_obj.browse(refund.get('res_id'))
        refund.lines.unlink()
        refund._onchange_amount_all()
        payment = self.pmp_obj.with_context(active_id=refund.id).create({
            'payment_method_id': refund.session_id.config_id.payment_method_ids[0].id,
            'amount': refund.amount_total,
        })
        payment.check()
        self.session.action_pos_session_closing_control()
        self.assertEqual(self.session.l10n_mx_edi_pac_status, "signed",
                         self.session.message_ids.mapped('body'))
        attach = self.att_obj.search([('res_model', '=', 'pos.session'), ('res_id', '=', self.session.id)]).datas
        xml_total = fromstring(base64.b64decode(attach)).get('Total')
        self.assertEqual(round(order.amount_total, 2), float(xml_total),
                         'The amount with include base amount is incorrect')

    def create_session(self):
        session = self.env.ref('point_of_sale.pos_config_main')
        session.sudo().available_pricelist_ids.currency_id = session.sudo().currency_id
        session = self.env['pos.session'].with_env(
            self.env(user=self.uid)).create({
                'user_id': self.uid,
                'config_id': session.id,
            })
        return session

    def create_order(self, partner=False, tax_included=False, autopaid=True):
        taxes = [self.tax_16.id, self.tax_10_negative.id]
        now = fields.Datetime.now('America/Mexico_City')
        order = self.pos_order_obj.with_env(self.env(user=self.uid)).create({
            'partner_id': partner or False,
            'session_id': self.session.id,
            'date_order': now,
            'pos_reference': 'Order %s - %s' % (
                self.session.id, len(self.session.order_ids)),
            'lines': [(0, 0, {
                'product_id': self.product.id,
                'price_unit': 100.0,
                'qty': 1.0,
                'tax_ids': [(6, 0, taxes)],
                'price_subtotal': 100.0,
                'price_subtotal_incl': 104.40 if tax_included else 106.0,
            })],
            'amount_total': 104.40 if tax_included else 106.0,
            'amount_tax': 4.40 if tax_included else 6.0,
            'amount_paid': 0.0,
            'amount_return': 0.0,
        })
        if autopaid:
            self.pay_order(order)
        return order

    def pay_order(self, order):
        payment = self.pmp_obj.with_env(self.env(user=self.uid)).with_context(
            active_id=order.id).create({
                'payment_method_id': order.session_id.config_id.payment_method_ids[0].id,  # noqa
                'amount': order.amount_total
            })
        payment.check()
        order.action_create_invoice()
        order.action_validate_invoice()
