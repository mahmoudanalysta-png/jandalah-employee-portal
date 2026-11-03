# -*- coding: utf-8 -*-
from odoo import models, fields, api

class HrEmployee(models.Model):
    _inherit = 'hr.employee'

    @api.model
    def get_user_employee(self, user=None):
        """Helper to find the employee record linked to the given user or current user with multi-criteria fallback."""
        if user is None:
            user = self.env.user
        emp = self.sudo().search([('user_id', '=', user.id)], order='id desc', limit=1)
        if not emp:
            user_email = (user.email or '').strip()
            user_login = (user.login or '').strip()
            domain = ['|', '|', '|',
                ('work_email', '=ilike', user_email),
                ('work_email', '=ilike', user_login),
                ('private_email', '=ilike', user_email),
                ('work_contact_id', '=', user.partner_id.id),
            ]
            emp = self.sudo().search(domain, order='id desc', limit=1)
            if emp and not emp.user_id:
                try:
                    emp.sudo().write({'user_id': user.id})
                except Exception:
                    pass

        if emp:
            try:
                if not emp.tz:
                    emp.sudo().write({'tz': 'Asia/Riyadh'})
                if user and not user.tz:
                    user.sudo().write({'tz': 'Asia/Riyadh'})
            except Exception:
                pass

        return emp

    def get_subordinates(self):
        """Returns all employees supervised or managed by this employee."""
        self.ensure_one()
        return self.sudo().search([
            '|', ('parent_id', '=', self.id), ('coach_id', '=', self.id)
        ])
