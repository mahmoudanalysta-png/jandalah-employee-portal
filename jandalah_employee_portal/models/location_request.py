# -*- coding: utf-8 -*-
from odoo import models, fields, api

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
    temp_radius_meters = fields.Integer(string='نطاق السماح (بالمتر)', default=500)

    state = fields.Selection([
        ('confirm', 'قيد مراجعة المشرف'),
        ('approve', 'معتمد'),
        ('refuse', 'مرفوض'),
    ], string='الحالة', default='confirm', required=True)

    supervisor_id = fields.Many2one('hr.employee', string='المشرف المعتمد')
    action_date = fields.Datetime(string='تاريخ اتخاذ القرار')
    refusal_reason = fields.Text(string='سبب الرفض')

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
                except Exception:
                    pass
        return records

    def action_approve(self):
        for rec in self:
            rec.write({
                'state': 'approve',
                'supervisor_id': self.env.user.employee_id.id if hasattr(self.env.user, 'employee_id') and self.env.user.employee_id else False,
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
            vals = {
                'state': 'refuse',
                'supervisor_id': self.env.user.employee_id.id if hasattr(self.env.user, 'employee_id') and self.env.user.employee_id else False,
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
