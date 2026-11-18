# -*- coding: utf-8 -*-
{
    'name': 'Jandalah Employee Self-Service Portal (بوابة موظفي جندله)',
    'summary': 'بوابة الخدمة الذاتية للموظفين - تسجيل حضور وانصراف بالـ GPS وطلبات الإجازات مجاناً لموظفي جندله',
    'version': '18.0.1.0.0',
    'category': 'Human Resources',
    'author': 'Jandalah Group',
    'website': 'https://jandalah.com.sa',
    'license': 'LGPL-3',
    'depends': [
        'base',
        'portal',
        'auth_signup',
        'hr',
        'hr_attendance',
        'hr_holidays',
    ],
    'data': [
        'security/ir.model.access.csv',
        'data/default_data.xml',
        'views/portal_templates.xml',
        'views/hr_views.xml',
    ],
    'assets': {
        'web.assets_backend': [
            'jandalah_employee_portal/static/src/css/backend_attendance.css',
        ],
    },
    'installable': True,
    'application': True,
    'auto_install': False,
}
