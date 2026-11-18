# -*- coding: utf-8 -*-
import logging
from odoo import models, fields, api

_logger = logging.getLogger(__name__)


class JandalahHrAssetCategory(models.Model):
    _name = 'jandalah.hr.asset.category'
    _description = 'تصنيف العهد ومستلزمات العمل'
    _order = 'sequence, id'

    name = fields.Char(string='تصنيف العهدة', required=True, translate=True)
    code = fields.Char(string='الكود')
    sequence = fields.Integer(string='الترتيب', default=10)
    active = fields.Boolean(string='نشط', default=True)


class JandalahHrAsset(models.Model):
    _name = 'jandalah.hr.asset'
    _description = 'العهد العينية والرقمية للموظفين'
    _order = 'handover_date desc, id desc'

    name = fields.Char(string='اسم العهدة / الوصف', required=True)
    asset_code = fields.Char(string='الرقم التسلسلي / كود العهدة')
    category_id = fields.Many2one('jandalah.hr.asset.category', string='تصنيف العهدة', ondelete='restrict')
    category = fields.Selection([
        ('laptop', 'حاسوب محمول / لابتوب'),
        ('mobile', 'هاتف محمول / شريحة اتصال'),
        ('car', 'سيارة شركة'),
        ('tools', 'أدوات ومعدات عمل ميداني'),
        ('digital', 'بريد إلكتروني / حساب رقمي'),
        ('other', 'عهدة أخرى'),
    ], string='تصنيف العهدة (قديم)')

    employee_id = fields.Many2one('hr.employee', string='الموظف المستلم', required=True, ondelete='cascade')
    handover_date = fields.Date(string='تاريخ تسليم العهدة', default=fields.Date.today, required=True)
    return_date = fields.Date(string='تاريخ استرداد العهدة الفعلي')
    notes = fields.Text(string='ملاحظات العهدة ومواصفاتها الفنية')

    attachment_file = fields.Binary(string='المرفق الرسمي للعهدة (PDF / صورة)', attachment=True)
    attachment_name = fields.Char(string='اسم الملف المرفق')

    state = fields.Selection([
        ('assigned', 'مسندة بانتظار إقرار الاستلام'),
        ('acknowledged', 'تم تأكيد الاستلام رسمياً'),
        ('return_requested', 'تم طلب الإرجاع من الموظف'),
        ('returned', 'تم إرجاع العهدة للشركة'),
    ], string='الحالة', default='assigned', required=True)

    # Receipt confirmation fields
    ack_date = fields.Datetime(string='تاريخ وساعة تأكيد الاستلام')
    ack_signature = fields.Char(string='إقرار الاستلام الإلكتروني')
    employee_feedback = fields.Text(string='إفادة وملاحظات الموظف عند الاستلام (Feedback)')

    # Return fields
    return_request_date = fields.Datetime(string='تاريخ طلب الإرجاع من البوابة')
    return_notes = fields.Text(string='ملاحظات الموظف عند طلب الإرجاع')
    returned_by_user_id = fields.Many2one('res.users', string='المسؤول مستلم العهدة عند الاسترداد')

    def action_confirm_return(self):
        """Action for HR to acknowledge return of the asset."""
        for rec in self:
            rec.write({
                'state': 'returned',
                'return_date': fields.Date.today(),
                'returned_by_user_id': self.env.user.id,
            })
            if rec.employee_id:
                try:
                    self.env['jandalah.push.subscriber'].sudo().notify_employee(
                        employee=rec.employee_id,
                        title="تم استلام وتأكيد إرجاع العهدة ✅",
                        body=f"تم تأكيد استلام عهدتك ({rec.name}) وإغلاقها رسمياً بنظام الشركة.",
                        url="/my/assets"
                    )
                except Exception:
                    pass

    @api.model
    def _register_hook(self):
        super()._register_hook()
        try:
            with self.env.cr.savepoint():
                self.env.cr.execute("""
                    UPDATE jandalah_hr_asset ja
                    SET category_id = jc.id
                    FROM jandalah_hr_asset_category jc
                    WHERE ja.category_id IS NULL AND ja.category = jc.code;
                """)
        except Exception:
            pass

    @api.model_create_multi
    def create(self, vals_list):
        records = super().create(vals_list)
        for rec in records:
            if rec.employee_id and rec.state == 'assigned':
                try:
                    self.env['jandalah.push.subscriber'].sudo().notify_employee(
                        employee=rec.employee_id,
                        title="إقرار استلام عهدة جديدة 💻",
                        body=f"تم إسناد عهدة جديدة لك: {rec.name}. يرجى تأكيد استلامها من البوابة.",
                        url="/my/assets"
                    )
                except Exception:
                    pass
        return records
