# -*- coding: utf-8 -*-

from odoo import models, _
from suds.client import Client
from zeep import Client
from zeep.transports import Transport


class AccountEdiFormat(models.Model):
    _inherit = "account.edi.format"

    def _l10n_mx_edi_finkok_cancel(self, move, credentials, cfdi):
        uuid_replace = move.l10n_mx_edi_cancel_invoice_id.l10n_mx_edi_cfdi_uuid
        return self._l10n_mx_edi_finkok_cancel_service(move.l10n_mx_edi_cfdi_uuid, move.company_id, credentials,
                                                       uuid_replace=uuid_replace, motivo=move.edi_motivo_cancel)

    def _l10n_mx_edi_finkok_cancel_service(self, uuid, company, credentials, uuid_replace=None, motivo=None):
        ''' Cancel the CFDI document with PAC: finkok. Does not depend on a recordset
        '''
        certificates = company.l10n_mx_edi_certificate_ids
        certificate = certificates.sudo().get_valid_certificate()
        cer_pem = certificate.get_pem_cer(certificate.content)
        key_pem = certificate.get_pem_key(certificate.key, certificate.password)
        try:
            transport = Transport(timeout=20)
            client = Client(credentials['cancel_url'], transport=transport)
            factory = client.type_factory('apps.services.soap.core.views')
            uuid_type = factory.UUID()
            uuid_type.UUID = uuid
            print("MOTIVO :::::: ", motivo)
            uuid_type.Motivo = "01" if uuid_replace else motivo
            if uuid_replace:
                uuid_type.FolioSustitucion = uuid_replace
            docs_list = factory.UUIDArray(uuid_type)
            response = client.service.cancel(
                docs_list,
                credentials['username'],
                credentials['password'],
                company.vat,
                cer_pem,
                key_pem,
            )
        except Exception as e:
            return {
                'errors': [_("The Finkok service failed to cancel with the following error: %s", str(e))],
            }

        if not getattr(response, 'Folios', None):
            code = getattr(response, 'CodEstatus', None)
            msg = _("Cancelling got an error") if code else _('A delay of 2 hours has to be respected before to cancel')
        else:
            code = getattr(response.Folios.Folio[0], 'EstatusUUID', None)
            cancelled = code in ('201', '202')  # cancelled or previously cancelled
            # no show code and response message if cancel was success
            code = '' if cancelled else code
            msg = '' if cancelled else _("Cancelling got an error")

        errors = []
        if code:
            errors.append(_("Code : %s") % code)
        if msg:
            errors.append(_("Message : %s") % msg)
        if errors:
            return {'errors': errors}

        return {'success': True}


    def _l10n_mx_edi_solfact_cancel(self, move, credentials, cfdi):
        uuid_replace = move.l10n_mx_edi_cancel_invoice_id.l10n_mx_edi_cfdi_uuid
        return self._l10n_mx_edi_solfact_cancel_service(move.l10n_mx_edi_cfdi_uuid, move.company_id, credentials,
                                                        uuid_replace=uuid_replace, motivo=move.edi_motivo_cancel)

    def _l10n_mx_edi_solfact_cancel_service(self, uuid, company, credentials, uuid_replace=None, motivo=None):
        ''' calls the Solucion Factible web service to cancel the document based on the UUID.
        Method does not depend on a recordset
        '''

        print("MOTIVO :::::: ", motivo)
        motivo = "01" if uuid_replace else motivo
        uuid = uuid + "|" + motivo + "|"
        if uuid_replace:
            uuid = uuid + uuid_replace
        certificates = company.l10n_mx_edi_certificate_ids
        certificate = certificates.sudo().get_valid_certificate()
        cer_pem = certificate.get_pem_cer(certificate.content)
        key_pem = certificate.get_pem_key(certificate.key, certificate.password)
        key_password = certificate.password

        try:
            transport = Transport(timeout=20)
            client = Client(credentials['url'], transport=transport)
            response = client.service.cancelar(
                credentials['username'], credentials['password'], uuid, cer_pem, key_pem, key_password)
        except Exception as e:
            return {
                'errors': [_("The Solucion Factible service failed to cancel with the following error: %s", str(e))],
            }

        if (response.status not in (200, 201)):
            # ws-timbrado-cancelar - status 200 : El proceso de cancelación se ha completado correctamente.
            # ws-timbrado-cancelar - status 201 : El folio se ha cancelado con éxito.
            return {
                'errors': [_("The Solucion Factible service failed to cancel with the following error: %s", response.mensaje)],
            }

        res = response.resultados
        code = getattr(res[0], 'statusUUID', None) if res else getattr(response, 'status', None)
        cancelled = code in ('201', '202')  # cancelled or previously cancelled
        # no show code and response message if cancel was success
        msg = '' if cancelled else getattr(res[0] if res else response, 'mensaje', None)
        code = '' if cancelled else code

        errors = []
        if code:
            errors.append(_("Code : %s") % code)
        if msg:
            errors.append(_("Message : %s") % msg)
        if errors:
            return {'errors': errors}

        return {'success': True}