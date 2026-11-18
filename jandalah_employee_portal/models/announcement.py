# -*- coding: utf-8 -*-
import base64
import logging
from odoo import models, fields, api

_logger = logging.getLogger(__name__)


class JandalahAnnouncementType(models.Model):
    _name = 'jandalah.announcement.type'
    _description = 'نوع التعميم / الإشعار'
    _order = 'sequence, id'

    name = fields.Char(string='نوع الإشعار', required=True, translate=True)
    code = fields.Char(string='الكود')
    sequence = fields.Integer(string='الترتيب', default=10)
    active = fields.Boolean(string='نشط', default=True)


class JandalahAnnouncement(models.Model):
    _name = 'jandalah.announcement'
    _description = 'تعاميم وقرارات إدارية'
    _order = 'date desc, id desc'

    name = fields.Char(string='عنوان التعميم / الخطاب', required=True)
    type_id = fields.Many2one('jandalah.announcement.type', string='نوع الإشعار', ondelete='restrict')
    announcement_type = fields.Selection([
        ('circular', 'تعميم إداري عام'),
        ('letter', 'خطاب رسمي'),
        ('decision', 'قرار إداري'),
        ('policy', 'لائحة وسياسة داخلية'),
    ], string='نوع الإشعار (قديم)')
    
    date = fields.Date(string='تاريخ الإصدار', default=fields.Date.today, required=True)
    issued_by_user_id = fields.Many2one('res.users', string='الجهة أو المسؤول المصدر', default=lambda self: self.env.user)
    issued_by = fields.Char(string='الجهة أو المسؤول المصدر (نص)', compute='_compute_issued_by', inverse='_inverse_issued_by', store=True)
    content = fields.Html(string='نص التعميم / القرار', required=True)
    
    target_type = fields.Selection([
        ('all', 'كافة الموظفين (كافة الشركات)'),
        ('company', 'شركة محددة'),
        ('department', 'قسم محدد داخل شركة'),
        ('employees', 'موظفون محددون'),
    ], string='الفئة المستهدفة', default='all', required=True)

    company_id = fields.Many2one('res.company', string='الشركة المستهدفة', default=lambda self: self.env.company)
    department_id = fields.Many2one('hr.department', string='القسم المستهدف')
    employee_ids = fields.Many2many('hr.employee', 'jandalah_announcement_emp_rel', 'announcement_id', 'employee_id', string='الموظفون المخصصون')
    
    attachment_id = fields.Many2one('ir.attachment', string='المرفق الرسمي (أرشيف قديم)')
    attachment_file = fields.Binary(string='المرفق الرسمي (PDF / صورة)', attachment=True)
    attachment_name = fields.Char(string='اسم الملف المرفق')
    active = fields.Boolean(string='نشط', default=True)

    @api.depends('issued_by_user_id')
    def _compute_issued_by(self):
        for rec in self:
            if rec.issued_by_user_id:
                rec.issued_by = rec.issued_by_user_id.name
            elif not rec.issued_by:
                rec.issued_by = 'الإدارة العامة'

    def _inverse_issued_by(self):
        for rec in self:
            if rec.issued_by and not rec.issued_by_user_id:
                user = self.env['res.users'].sudo().search([('name', '=ilike', rec.issued_by.strip())], limit=1)
                if user:
                    rec.issued_by_user_id = user.id

    def get_official_attachment_info(self):
        """Safely retrieves attachment metadata and data whether stored in binary field, Many2one, or ir.attachment."""
        self.ensure_one()
        # 1. Direct binary field on model (only if content exists)
        rec_no_bin = self.with_context(bin_size=False)
        if rec_no_bin.attachment_file:
            name = rec_no_bin.attachment_name or f"announcement_{self.id}.pdf"
            if '.' not in name:
                name = f"{name}.pdf"
            return {
                'has_attachment': True,
                'data': rec_no_bin.attachment_file,
                'name': name,
                'source': 'binary',
                'id': None,
            }

        # 2. Many2one attachment_id (user uploaded directly into attachment_id)
        if self.attachment_id:
            try:
                att = self.attachment_id.sudo().with_context(bin_size=False)
                name = att.name or f"announcement_{self.id}.pdf"
                if name != 'loader.js' and not name.lower().endswith('.js'):
                    if '.' not in name:
                        name = f"{name}.pdf"
                    datas = att.datas
                    if not datas and getattr(att, 'raw', False):
                        datas = base64.b64encode(att.raw).decode('utf-8')
                    if not datas and getattr(att, 'store_fname', False):
                        try:
                            file_bytes = att._file_read(att.store_fname)
                            if file_bytes:
                                datas = base64.b64encode(file_bytes).decode('utf-8')
                        except Exception as e_filestore:
                            _logger.warning("Error reading filestore: %s", e_filestore)

                    return {
                        'has_attachment': True,
                        'data': datas,
                        'name': name,
                        'source': 'attachment_id',
                        'id': att.id,
                    }
            except Exception as e:
                _logger.warning("Error reading announcement attachment_id: %s", e)

        # 3. Linked ir.attachment (e.g. uploaded via chatter or control panel)
        try:
            Att = self.env['ir.attachment'].sudo().with_context(bin_size=False)
            att = Att.search([
                ('res_model', '=', 'jandalah.announcement'),
                ('res_id', '=', self.id),
                ('name', '!=', 'loader.js'),
                ('name', 'not like', '%.js'),
            ], order='id desc', limit=1)
            if att:
                name = att.name or f"announcement_{self.id}.pdf"
                if '.' not in name:
                    name = f"{name}.pdf"
                datas = att.datas
                if not datas and getattr(att, 'raw', False):
                    datas = base64.b64encode(att.raw).decode('utf-8')
                if not datas and getattr(att, 'store_fname', False):
                    try:
                        file_bytes = att._file_read(att.store_fname)
                        if file_bytes:
                            datas = base64.b64encode(file_bytes).decode('utf-8')
                    except Exception:
                        pass
                return {
                    'has_attachment': True,
                    'data': datas,
                    'name': name,
                    'source': 'ir_attachment',
                    'id': att.id,
                }
        except Exception as e:
            _logger.warning("Error searching linked ir.attachment: %s", e)

        return {'has_attachment': False, 'data': None, 'name': None, 'source': None, 'id': None}

    def init(self):
        super().init()
        # Seed default announcement types if table exists and empty
        try:
            self.env.cr.execute("""
                CREATE TABLE IF NOT EXISTS jandalah_announcement_type (
                    id serial PRIMARY KEY,
                    name varchar NOT NULL,
                    code varchar,
                    sequence integer DEFAULT 10,
                    active boolean DEFAULT true,
                    create_date timestamp without time zone,
                    write_date timestamp without time zone,
                    create_uid integer,
                    write_uid integer
                );
            """)
            self.env.cr.execute("SELECT count(*) FROM jandalah_announcement_type;")
            count = self.env.cr.fetchone()[0]
            if count == 0:
                self.env.cr.execute("""
                    INSERT INTO jandalah_announcement_type (name, code, sequence, active, create_date, write_date)
                    VALUES 
                    ('تعميم إداري عام', 'circular', 10, true, NOW(), NOW()),
                    ('خطاب رسمي', 'letter', 20, true, NOW(), NOW()),
                    ('قرار إداري', 'decision', 30, true, NOW(), NOW()),
                    ('لائحة وسياسة داخلية', 'policy', 40, true, NOW(), NOW()),
                    ('تهنئة / مناسبة', 'greeting', 50, true, NOW(), NOW()),
                    ('تنبيه هام', 'warning', 60, true, NOW(), NOW());
                """)

            # Migrate existing records type_id from announcement_type
            self.env.cr.execute("""
                DO $$
                BEGIN
                    IF EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name='jandalah_announcement' AND column_name='type_id') THEN
                        UPDATE jandalah_announcement ja
                        SET type_id = jt.id
                        FROM jandalah_announcement_type jt
                        WHERE ja.type_id IS NULL AND ja.announcement_type = jt.code;
                    END IF;
                END $$;
            """)
        except Exception as e_seed:
            _logger.warning("Could not seed jandalah_announcement_type: %s", e_seed)

        try:
            self.env.cr.execute("""
                UPDATE ir_ui_view 
                SET active = false 
                WHERE model = 'jandalah.announcement' 
                  AND type = 'form' 
                  AND inherit_id IS NOT NULL;

                UPDATE ir_ui_view 
                SET active = false 
                WHERE inherit_id IN (
                    SELECT res_id 
                    FROM ir_model_data 
                    WHERE model = 'ir.ui.view' 
                      AND module = 'jandalah_employee_portal' 
                      AND name = 'view_jandalah_announcement_form'
                );
            """)
        except Exception as e:
            _logger.warning("Error cleaning custom inherited views: %s", e)

    @api.model_create_multi
    def create(self, vals_list):
        records = super().create(vals_list)
        for rec in records:
            if rec.active:
                try:
                    title = f"تعميم إداري جديد: {rec.name}"
                    issuer_str = rec.issued_by or (rec.issued_by_user_id.name if rec.issued_by_user_id else 'الإدارة')
                    body = f"صادر عن: {issuer_str}. يرجى الاطلاع والإقرار."
                    if rec.target_type == 'all':
                        self.env['jandalah.push.subscriber'].sudo().notify_all_employees(
                            title=title, body=body, url='/my/announcements'
                        )
                    elif rec.target_type == 'company' and rec.company_id:
                        emps = self.env['hr.employee'].sudo().search([('company_id', '=', rec.company_id.id)])
                        for emp in emps:
                            self.env['jandalah.push.subscriber'].sudo().notify_employee(
                                employee=emp, title=title, body=body, url='/my/announcements'
                            )
                    elif rec.target_type == 'department' and rec.department_id:
                        dom = [('department_id', '=', rec.department_id.id)]
                        if rec.company_id:
                            dom.append(('company_id', '=', rec.company_id.id))
                        emps = self.env['hr.employee'].sudo().search(dom)
                        for emp in emps:
                            self.env['jandalah.push.subscriber'].sudo().notify_employee(
                                employee=emp, title=title, body=body, url='/my/announcements'
                            )
                    elif rec.target_type == 'employees':
                        for emp in rec.employee_ids:
                            self.env['jandalah.push.subscriber'].sudo().notify_employee(
                                employee=emp, title=title, body=body, url='/my/announcements'
                            )
                except Exception as e_push:
                    _logger.warning("Error pushing new announcement: %s", e_push)
        return records

    ack_ids = fields.One2many('jandalah.announcement.ack', 'announcement_id', string='إقرارات الاطلاع')
    ack_count = fields.Integer(string='عدد المطلعين', compute='_compute_ack_count')

    def _compute_ack_count(self):
        for rec in self:
            rec.ack_count = len(rec.ack_ids)

    def is_acknowledged_by(self, employee):
        """Checks whether the given employee has acknowledged reading this announcement."""
        self.ensure_one()
        if not employee:
            return False
        return bool(self.env['jandalah.announcement.ack'].sudo().search_count([
            ('announcement_id', '=', self.id),
            ('employee_id', '=', employee.id)
        ]))

    def is_visible_for(self, employee):
        """Checks if this announcement is targeted to the given employee."""
        self.ensure_one()
        if not employee or not self.active:
            return False
        if self.target_type == 'all':
            return True
        elif self.target_type == 'company' and self.company_id:
            return employee.company_id.id == self.company_id.id
        elif self.target_type == 'department' and self.department_id:
            dept_match = (employee.department_id.id == self.department_id.id)
            if self.company_id:
                return dept_match and (employee.company_id.id == self.company_id.id)
            return dept_match
        elif self.target_type == 'employees':
            return employee.id in self.employee_ids.ids
        return False


class JandalahAnnouncementAck(models.Model):
    _name = 'jandalah.announcement.ack'
    _description = 'إقرار قراءة التعميم'
    _order = 'ack_date desc'

    announcement_id = fields.Many2one('jandalah.announcement', string='التعميم', required=True, ondelete='cascade')
    employee_id = fields.Many2one('hr.employee', string='الموظف', required=True, ondelete='cascade')
    ack_date = fields.Datetime(string='تاريخ وساعة الإقرار', default=fields.Datetime.now, required=True)

    _sql_constraints = [
        ('emp_announcement_uniq', 'unique(announcement_id, employee_id)', 'لقد قمت بتأكيد الاطلاع على هذا التعميم مسبقاً.')
    ]
