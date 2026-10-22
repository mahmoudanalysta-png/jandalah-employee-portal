# -*- coding: utf-8 -*-
from odoo import models, fields, api

class HrEmployee(models.Model):
    _inherit = 'hr.employee'

    @api.model
    def get_user_employee(self, user=None):
        """Helper to find the employee record linked to the given user or current user."""
        if user is None:
            user = self.env.user
        emp = self.sudo().search([('user_id', '=', user.id)], limit=1)
        if not emp and user.email:
            emp = self.sudo().search([('work_email', '=ilike', user.email.strip())], limit=1)
            if emp and not emp.user_id:
                emp.sudo().write({'user_id': user.id})
        return emp
