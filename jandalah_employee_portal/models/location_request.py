# -*- coding: utf-8 -*-
import logging
from odoo import models, fields, api, exceptions, _

_logger = logging.getLogger(__name__)


class JandalahLocationRequest(models.Model):
    _name = 'jandalah.location.request'
    _description = 'طلبات العمل من موقع مؤقت / بديل'
    _order = 'date_from desc, id desc'

    employee_id = fields.Many2one('hr.employee', string='الموظف', required=True, ondelete='cascade')
    date_from = fields.Date(string='من تاريخ', default=fields.Date.today, required=True)
    date_to = fields.Date(string='إلى تاريخ', default=fields.Date.today, required=True)
    temp_location_name = fields.Char(string='اسم الموقع المؤقت (المدينة / الحي)', required=True)
    reason = fields.Text(string='سبب طلب العمل من موقع بديل', required=True)
    
    temp_latitude = fields.Float(string='خط عرض الموقع المؤقت', digits=(10, 7))
    temp_longitude = fields.Float(string='خط طول الموقع المؤقت', digits=(10, 7))
    temp_radius_meters = fields.Integer(string='نطاق السماح (بالمتر)', default=1000)

    state = fields.Selection([
        ('confirm', 'قيد مراجعة المشرف'),
        ('approve', 'معتمد'),
        ('refuse', 'مرفوض'),
    ], string='الحالة', default='confirm', required=True)

    supervisor_id = fields.Many2one('hr.employee', string='المشرف المعتمد')
    action_date = fields.Datetime(string='تاريخ اتخاذ القرار')
    refusal_reason = fields.Text(string='سبب الرفض')

    @api.constrains('date_from', 'date_to')
    def _check_location_request_dates(self):
        today = fields.Date.today()
        for rec in self:
            if rec.date_from and rec.date_from < today:
                raise exceptions.ValidationError(_("لا يمكن تقديم طلب عمل من موقع مؤقت بتاريخ سابق لتاريخ اليوم."))
            if rec.date_from and rec.date_to and rec.date_to < rec.date_from:
                raise exceptions.ValidationError(_("تاريخ النهاية يجب ألا يكون قبل تاريخ البداية."))

    @api.model_create_multi
    def create(self, vals_list):
        records = super().create(vals_list)
        for rec in records:
            if rec.employee_id and rec.state == 'confirm':
                try:
                    supervisor = rec.employee_id.parent_id or rec.employee_id.coach_id
                    if supervisor:
                        self.env['jandalah.push.subscriber'].sudo().notify_employee(
                            employee=supervisor,
                            title="طلب موقع ميداني جديد 📍",
                            body=f"الموظف {rec.employee_id.name} يطلب اعتماد العمل من موقع ({rec.temp_location_name})",
                            url="/my/team/locations"
                        )
                    else:
                        # Fallback to HR admin officers if no supervisor is assigned
                        hr_group = self.env.ref('hr.group_hr_user', raise_if_not_found=False)
                        if hr_group and hr_group.users:
                            for u in hr_group.users:
                                if getattr(u, 'employee_id', False):
                                    self.env['jandalah.push.subscriber'].sudo().notify_employee(
                                        employee=u.employee_id,
                                        title="طلب موقع ميداني جديد (بدون مشرف) 📍",
                                        body=f"الموظف {rec.employee_id.name} يطلب اعتماد العمل من موقع ({rec.temp_location_name})",
                                        url="/my/team/locations"
                                    )
                except Exception as e_push:
                    _logger.warning("Error pushing location request: %s", e_push)
        return records

    def action_approve(self):
        for rec in self:
            emp = self.env.user.employee_id if hasattr(self.env.user, 'employee_id') and self.env.user.employee_id else (
                self.env['hr.employee'].sudo().search([('user_id', '=', self.env.user.id)], limit=1)
            )
            rec.write({
                'state': 'approve',
                'supervisor_id': emp.id if emp else False,
                'action_date': fields.Datetime.now(),
            })
            if rec.employee_id:
                try:
                    self.env['jandalah.push.subscriber'].sudo().notify_employee(
                        employee=rec.employee_id,
                        title="تم اعتماد موقعك الميداني ✅",
                        body=f"تمت الموافقة على طلب تواجدك في ({rec.temp_location_name}) من {rec.date_from}.",
                        url="/my"
                    )
                except Exception:
                    pass

    def action_refuse(self, reason=None):
        for rec in self:
            emp = self.env.user.employee_id if hasattr(self.env.user, 'employee_id') and self.env.user.employee_id else (
                self.env['hr.employee'].sudo().search([('user_id', '=', self.env.user.id)], limit=1)
            )
            vals = {
                'state': 'refuse',
                'supervisor_id': emp.id if emp else False,
                'action_date': fields.Datetime.now(),
            }
            if reason:
                vals['refusal_reason'] = reason
            rec.write(vals)
            if rec.employee_id:
                try:
                    reason_msg = f" - السبب: {rec.refusal_reason}" if rec.refusal_reason else ""
                    self.env['jandalah.push.subscriber'].sudo().notify_employee(
                        employee=rec.employee_id,
                        title="تم رفض طلب الموقع الميداني ❌",
                        body=f"عذراً، لم تتم الموافقة على طلبك في ({rec.temp_location_name}){reason_msg}",
                        url="/my"
                    )
                except Exception:
                    pass
