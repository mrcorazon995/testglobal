# -*- coding: utf-8 -*-
{
    'name': "l10n_mx_edi_cancel_extended",

    'summary': """Extends cancel functionality for cfdi 4.0 purposes""",

    'description': """
    
    """,

    'author': "My Company",
    'website': "http://www.yourcompany.com",

    # Categories can be used to filter modules in modules listing
    # Check https://github.com/odoo/odoo/blob/14.0/odoo/addons/base/data/ir_module_category_data.xml
    # for the full list
    'category': 'Uncategorized',
    'version': '0.1',

    # any module necessary for this one to work correctly
    'depends': ['base','account','l10n_mx_edi'],

    # always loaded
    'data': [
        'report/report.xml',
        'views/views.xml',
    ],
}
