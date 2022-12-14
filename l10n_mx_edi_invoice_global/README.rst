Mexican POS Management System
=============================

When is generated a new sale from point of sale, the user have three options
before to validate the order:

1. Not assign customer in the sale.
2. Assign a customer in the sale.
3. Assign customer in the sale, and generate the invoice.

.. image:: l10n_mx_edi_invoice_global/static/src/img/cases_pos.png

This module adds a new button that allow generate the XML files related with
the session, after that it is closed.

The XML file take the data from all orders related with the session depending
in which case is each order.

**What makes this module depending on the case?**

**CASE 1:**

A CFDI is generated with all orders that have not partner assigned, for a
generic partner, and is sent to sign with the PAC assigned in the company.

The resultant XML is attached in the section.

**CASE 2:**

A CFDI is generated with all orders that have a partner assigned but that
has not all the data to generate a CFDI (Country and VAT). Is for a
generic partner, and is sent to sign with the PAC assigned in the company.

The resultant XML is attached in the section.

**CASE 3:**

The orders that are assigned to a partner, and this have configured the
country and the VAT. When the order is generated, the invoice is created,
this as the Odoo process.

Configuration
=============

* To comply with the requirements of the SAT regarding the "FormaPago"
  key in the CFDI, the payment way must be configured in the journal
  used at the point of sale.

.. image:: l10n_mx_edi_invoice_global/static/src/img/journal.png
   :align: center
   :width: 200pt
