# Part of Odoo. See LICENSE file for full copyright and licensing details.

import logging

from odoo import models

_logger = logging.getLogger(__name__)


class SessionXmlReport(models.AbstractModel):
    _name = "report.l10n_mx_edi_invoice_global.report_xml_session"
    _description = "XML report for POS session"

    def _get_report_values(self, docids, data=None):
        sessions = self.env['global.invoice'].browse(docids)
        return {
            'doc_ids': docids,
            'doc_model': 'global.invoice',
            'docs': sessions,
            'cfdi': data.get('cfdi', ''),
        }
