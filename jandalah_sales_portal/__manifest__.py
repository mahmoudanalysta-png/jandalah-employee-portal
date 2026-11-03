# -*- coding: utf-8 -*-
{
    'name': 'Jandalah Van Sales & Mobile Portal (بوابة مبيعات وتحصيل سيارات التوزيع)',
    'summary': 'تطبيق جوال متقدم لمناديب المبيعات وسيارات التوزيع: فواتير بيع، رصيد العميل، حد ائتماني، تحصيل، جرد سيارة، وجرد رئيسي لشركتي القناديل المشرقة وتجارة المواد',
    'version': '18.0.1.0.0',
    'category': 'Sales',
    'author': 'Jandalah Group',
    'website': 'https://jandalah.com.sa',
    'license': 'LGPL-3',
    'depends': [
        'base',
        'portal',
        'sale',
        'account',
        'stock',
    ],
    'data': [
        'security/ir.model.access.csv',
        'views/sales_portal_templates.xml',
    ],
    'installable': True,
    'application': True,
    'auto_install': False,
}
