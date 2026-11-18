# -*- coding: utf-8 -*-
import base64
import logging
from odoo import models, fields, api

_logger = logging.getLogger(__name__)


class JandalahHrPenaltyType(models.Model):
    _name = 'jandalah.hr.penalty.type'
    _description = 'نوع الجزاء / الإجراء الإداري'
    _order = 'sequence, id'

    name = fields.Char(string='نوع الإجراء', required=True, translate=True)
    code = fields.Char(string='الكود')
    sequence = fields.Integer(string='الترتيب', default=10)
    active = fields.Boolean(string='نشط', default=True)


class JandalahHrPenalty(models.Model):
    _name = 'jandalah.hr.penalty'
    _description = 'جزاءات وتحقيقات إدارية'
    _order = 'date desc, id desc'

    name = fields.Char(string='رقم / مرجع القرار', required=True, default='PEN')
    employee_id = fields.Many2one('hr.employee', string='الموظف المعني', required=True, ondelete='cascade')
    type_id = fields.Many2one('jandalah.hr.penalty.type', string='نوع الإجراء', ondelete='restrict')
    penalty_type = fields.Selection([
        ('warning', 'لفت نظر / إنذار كتابي'),
        ('deduction', 'خصم من الراتب'),
        ('investigation', 'إحالة للتحقيق الإداري'),
        ('suspension', 'إيقاف مؤقت عن العمل'),
    ], string='نوع الإجراء (قديم)')

    date = fields.Date(string='تاريخ القرار / الإحالة', default=fields.Date.today, required=True)
    issued_by_user_id = fields.Many2one('res.users', string='الجهة أو المسؤول المصدر', default=lambda self: self.env.user)
    issued_by = fields.Char(string='الجهة أو المسؤول المصدر (نص)', compute='_compute_issued_by', inverse='_inverse_issued_by', store=True)
    reason = fields.Text(string='الموضوع / سبب الإجراء أو التحقيق', required=True)
    deduction_days = fields.Float(string='عدد أيام الخصم', default=0.0)

    state = fields.Selection([
        ('issued', 'صادر وبانتظار رد الموظف'),
        ('accepted', 'تم الإقرار والقبول'),
        ('objected', 'تم تسجيل اعتراض وإفادة'),
        ('closed', 'مغلق ومحفوظ'),
    ], string='الحالة', default='issued', required=True)

    employee_response = fields.Selection([
        ('accept', 'قبول وإقرار بالجزاء'),
        ('object', 'اعتراض وعدم موافقة'),
    ], string='رد الموظف')

    employee_comment = fields.Text(string='إفادة وتبرير الموظف')
    response_date = fields.Datetime(string='تاريخ وساعة الرد')
    attachment_id = fields.Many2one('ir.attachment', string='المستند الرسمي (أرشيف قديم)')
    attachment_file = fields.Binary(string='المستند الرسمي (PDF / صورة)', attachment=True)
    attachment_name = fields.Char(string='اسم الملف المرفق')

    @api.depends('issued_by_user_id')
    def _compute_issued_by(self):
        for rec in self:
            if rec.issued_by_user_id:
                rec.issued_by = rec.issued_by_user_id.name
            elif not rec.issued_by:
                rec.issued_by = 'إدارة الموارد البشرية'

    def _inverse_issued_by(self):
        for rec in self:
            if rec.issued_by and not rec.issued_by_user_id:
                user = self.env['res.users'].sudo().search([('name', '=ilike', rec.issued_by.strip())], limit=1)
                if user:
                    rec.issued_by_user_id = user.id

    @api.model_create_multi
    def create(self, vals_list):
        records = super().create(vals_list)
        for rec in records:
            if rec.employee_id:
                try:
                    p_type = rec.type_id.name if rec.type_id else (
                        dict(rec._fields['penalty_type'].selection).get(rec.penalty_type, 'إجراء إداري') if rec.penalty_type else 'إجراء إداري'
                    )
                    self.env['jandalah.push.subscriber'].sudo().notify_employee(
                        employee=rec.employee_id,
                        title="تنبيه رسمي من الشؤون الإدارية ⚠️",
                        body=f"صدر بحقك {p_type} برقم ({rec.name})، يرجى الدخول للإفادة والإقرار.",
                        url="/my/penalties"
                    )
                except Exception as e_pen_push:
                    _logger.warning("Error pushing penalty notification: %s", e_pen_push)
        return records

    def get_official_attachment_info(self):
        """Safely retrieves attachment metadata and data for penalty."""
        self.ensure_one()
        # 1. Direct binary field on model (only if content exists)
        if self.attachment_file:
            name = self.attachment_name or f"penalty_{self.id}.pdf"
            if '.' not in name:
                name = f"{name}.pdf"
            return {
                'has_attachment': True,
                'data': self.attachment_file,
                'name': name,
                'source': 'binary',
                'id': None,
            }

        # 2. Many2one attachment_id
        if self.attachment_id:
            try:
                att = self.attachment_id.sudo()
                name = att.name or f"penalty_{self.id}.pdf"
                if name != 'loader.js' and not name.lower().endswith('.js'):
                    if '.' not in name:
                        name = f"{name}.pdf"
                    datas = att.datas
                    if not datas and getattr(att, 'raw', False):
                        datas = base64.b64encode(att.raw).decode('utf-8')
                    return {
                        'has_attachment': True,
                        'data': datas,
                        'name': name,
                        'source': 'attachment_id',
                        'id': att.id,
                    }
            except Exception as e:
                _logger.warning("Error reading penalty attachment_id: %s", e)

        # 3. Linked ir.attachment
        try:
            Att = self.env['ir.attachment'].sudo()
            att = Att.search([
                ('res_model', '=', 'jandalah.hr.penalty'),
                ('res_id', '=', self.id),
                ('name', '!=', 'loader.js'),
                ('name', 'not like', '%.js'),
            ], order='id desc', limit=1)
            if att:
                name = att.name or f"penalty_{self.id}.pdf"
                if '.' not in name:
                    name = f"{name}.pdf"
                datas = att.datas
                if not datas and getattr(att, 'raw', False):
                    datas = base64.b64encode(att.raw).decode('utf-8')
                return {
                    'has_attachment': True,
                    'data': datas,
                    'name': name,
                    'source': 'ir_attachment',
                    'id': att.id,
                }
        except Exception as e:
            _logger.warning("Error searching penalty linked ir.attachment: %s", e)

        return {'has_attachment': False, 'data': None, 'name': None, 'source': None, 'id': None}

    def init(self):
        super().init()
        try:
            with self.env.cr.savepoint():
                self.env.cr.execute("""
                    UPDATE ir_ui_view 
                    SET active = false 
                    WHERE model = 'jandalah.hr.penalty' 
                      AND type = 'form' 
                      AND inherit_id IS NOT NULL;

                    UPDATE ir_ui_view 
                    SET active = false 
                    WHERE inherit_id IN (
                        SELECT res_id 
                        FROM ir_model_data 
                        WHERE model = 'ir.ui.view' 
                          AND module = 'jandalah_employee_portal' 
                          AND name = 'view_jandalah_penalty_form'
                    );
                """)
        except Exception as e:
            _logger.warning("Error cleaning custom penalty inherited views: %s", e)

    @api.model
    def _register_hook(self):
        super()._register_hook()
        try:
            with self.env.cr.savepoint():
                self.env.cr.execute("""
                    UPDATE jandalah_hr_penalty jp
                    SET type_id = jpt.id
                    FROM jandalah_hr_penalty_type jpt
                    WHERE jp.type_id IS NULL AND jp.penalty_type = jpt.code;
                """)
        except Exception:
            pass
