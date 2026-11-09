# -*- coding: utf-8 -*-
import math
import datetime
import pytz
from odoo import models, fields, api, exceptions, _

class HrEmployee(models.Model):
    _inherit = 'hr.employee'

    # Geofencing configuration
    geofence_type = fields.Selection([
        ('office', 'مقر الشركة الرئيسي'),
        ('home', 'العمل عن بعد من المنزل'),
        ('field', 'عمل ميداني حر'),
    ], string='طبيعة موقع العمل', default='office')
    
    work_latitude = fields.Float(string='خط عرض موقع العمل المعتمد', digits=(10, 7))
    work_longitude = fields.Float(string='خط طول موقع العمل المعتمد', digits=(10, 7))
    work_radius_meters = fields.Integer(string='نطاق السماح الجغرافي (بالمتر)', default=500,
                                       help='المسافة المسموح بها حول الموقع بالأمتار (0 = غير مقيد)')

    def calculate_distance_meters(self, lat1, lon1, lat2, lon2):
        """Calculates spherical distance in meters between two coordinates using Haversine formula."""
        if not lat1 or not lon1 or not lat2 or not lon2:
            return 0.0
        try:
            r = 6371000.0  # Earth radius in meters
            phi1 = math.radians(lat1)
            phi2 = math.radians(lat2)
            delta_phi = math.radians(lat2 - lat1)
            delta_lambda = math.radians(lon2 - lon1)

            a = math.sin(delta_phi / 2.0) ** 2 + \
                math.cos(phi1) * math.cos(phi2) * math.sin(delta_lambda / 2.0) ** 2
            c = 2.0 * math.atan2(math.sqrt(a), math.sqrt(1.0 - a))
            return r * c
        except Exception:
            return 0.0

    def get_effective_work_location(self, target_date=None):
        """Returns the active work location for this employee on target_date,
        taking into account approved temporary location requests."""
        self.ensure_one()
        if not target_date:
            target_date = fields.Date.today()
        
        # Check if there is an approved temporary location request for this date
        Req = self.env['jandalah.location.request'].sudo()
        active_req = Req.search([
            ('employee_id', '=', self.id),
            ('state', '=', 'approve'),
            ('date_from', '<=', target_date),
            ('date_to', '>=', target_date)
        ], limit=1)

        if active_req and active_req.temp_latitude and active_req.temp_longitude:
            return {
                'is_temporary': True,
                'location_name': active_req.temp_location_name or 'موقع عمل مؤقت معتمد',
                'latitude': active_req.temp_latitude,
                'longitude': active_req.temp_longitude,
                'radius': active_req.temp_radius_meters or self.work_radius_meters or 500,
            }

        geo_type = self.sudo().geofence_type or 'office'
        return {
            'is_temporary': False,
            'location_name': 'ميداني حر (غير مقيد)' if geo_type == 'field' else ('العمل عن بعد من المنزل' if geo_type == 'home' else 'المقر المعتمد للشركة'),
            'latitude': float(self.sudo().work_latitude or 0.0),
            'longitude': float(self.sudo().work_longitude or 0.0),
            'radius': int(self.sudo().work_radius_meters or 0) if geo_type != 'field' else 0,
        }

    @api.model
    def get_user_employee(self, user=None):
        """Helper to find the employee record linked to the given user or current user with multi-criteria fallback."""
        if user is None:
            user = self.env.user
        company = self.env.company
        emp = self.sudo().search([
            ('user_id', '=', user.id),
            ('company_id', '=', company.id)
        ], order='id desc', limit=1)
        if not emp:
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
            emp = self.sudo().search([('company_id', '=', company.id)] + domain, order='id desc', limit=1)
            if not emp:
                emp = self.sudo().search(domain, order='id desc', limit=1)

            if emp and not emp.user_id:
                existing = self.sudo().search_count([
                    ('user_id', '=', user.id),
                    ('company_id', '=', emp.company_id.id)
                ])
                if existing == 0:
                    try:
                        with self.env.cr.savepoint():
                            emp.sudo().write({'user_id': user.id})
                    except Exception:
                        pass

        if emp:
            try:
                if not emp.tz:
                    with self.env.cr.savepoint():
                        emp.sudo().write({'tz': 'Asia/Riyadh'})
                if user and not user.tz:
                    with self.env.cr.savepoint():
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


class HrAttendance(models.Model):
    _inherit = 'hr.attendance'

    distance_from_work = fields.Float(string='المسافة عن موقع العمل (متر)', digits=(10, 2))
    is_outside_geofence = fields.Boolean(string='خارج النطاق الجغرافي المسموح', default=False)

    @api.constrains('check_in', 'check_out', 'employee_id')
    def _check_validity(self):
        """Allows new day check-in even if previous day check-out is omitted (Midnight rule)."""
        for attendance in self:
            att_check_in = attendance.check_in
            att_check_out = attendance.check_out
            if not att_check_in:
                continue

            domain = [
                ('employee_id', '=', attendance.employee_id.id),
                ('id', '!=', attendance.id),
            ]

            if att_check_out:
                domain += [
                    ('check_in', '<', att_check_out),
                    ('check_out', '>', att_check_in),
                ]
                conflicts = self.search_count(domain)
                if conflicts:
                    raise exceptions.ValidationError(
                        _("Cannot create overlapping attendance records for %(empl_name)s.") % {
                            'empl_name': attendance.employee_id.name,
                        }
                    )
            else:
                att_date = att_check_in.date()
                domain += [
                    ('check_out', '=', False),
                    ('check_in', '>=', fields.Datetime.to_datetime(att_date)),
                    ('check_in', '<', fields.Datetime.to_datetime(att_date) + datetime.timedelta(days=1)),
                ]
                conflicts = self.search_count(domain)
                if conflicts:
                    raise exceptions.ValidationError(
                        _("Employee %(empl_name)s already has an open attendance record today.") % {
                            'empl_name': attendance.employee_id.name,
                        }
                    )

    # --- Jehat Comprehensive Attendance Report Fields (Matching Management Blueprint) ---
    in_location_state = fields.Selection([
        ('approved', 'معتمد'),
        ('rejected', 'غير معتمد'),
        ('none', '-'),
    ], string='مكان الحضور', compute='_compute_jehat_report_fields', store=True)

    out_location_state = fields.Selection([
        ('approved', 'معتمد'),
        ('rejected', 'غير معتمد'),
        ('none', '-'),
    ], string='مكان الانصراف', compute='_compute_jehat_report_fields', store=True)

    morning_delay_minutes = fields.Integer(
        string='دقائق التأخير',
        compute='_compute_jehat_report_fields',
        store=True
    )

    delay_display = fields.Char(
        string='التأخير الصباحي',
        compute='_compute_jehat_report_fields',
        store=True
    )

    late_deduction = fields.Float(
        string='خصم التأخير',
        compute='_compute_jehat_report_fields',
        store=True,
        digits=(10, 2)
    )

    missing_checkout_deduction = fields.Float(
        string='خصم الانصراف',
        compute='_compute_jehat_report_fields',
        store=True,
        digits=(10, 2)
    )

    net_deduction = fields.Float(
        string='صافي الخصم',
        compute='_compute_jehat_report_fields',
        store=True,
        digits=(10, 2)
    )

    occupancy_rate = fields.Float(
        string='نسبة الإشغال %',
        compute='_compute_jehat_report_fields',
        store=True,
        digits=(10, 1)
    )

    attendance_notes = fields.Char(
        string='ملاحظات',
        compute='_compute_jehat_report_fields',
        store=True
    )

    @api.depends('check_in', 'check_out', 'employee_id', 'is_outside_geofence')
    def _compute_jehat_report_fields(self):
        riyadh_tz = pytz.timezone('Asia/Riyadh')
        now_utc = datetime.datetime.utcnow()
        now_local = pytz.utc.localize(now_utc).astimezone(riyadh_tz)
        today_local_date = now_local.date()

        for att in self:
            # 1. Location state
            has_in_coords = hasattr(att, 'in_latitude') and att.in_latitude and att.in_longitude
            if has_in_coords:
                att.in_location_state = 'rejected' if att.is_outside_geofence else 'approved'
            elif att.check_in:
                att.in_location_state = 'approved'
            else:
                att.in_location_state = 'none'

            has_out_coords = hasattr(att, 'out_latitude') and att.out_latitude and att.out_longitude
            if has_out_coords:
                att.out_location_state = 'approved'
            elif att.check_out:
                att.out_location_state = 'approved'
            else:
                att.out_location_state = 'none'

            # 2. Check-in & morning delay calculation (19-min grace after 8:00 AM)
            delay_min = 0
            late_ded = 0.0
            if att.check_in:
                in_dt = att.check_in
                if not getattr(in_dt, 'tzinfo', None):
                    in_dt = pytz.utc.localize(in_dt)
                in_local = in_dt.astimezone(riyadh_tz)

                punch_min = in_local.hour * 60 + in_local.minute
                work_start_min = 8 * 60  # 8:00 AM
                grace_min = 19
                if punch_min > (work_start_min + grace_min):
                    delay_min = punch_min - work_start_min
                    if delay_min <= 60:
                        late_ded = 0.25
                    else:
                        late_ded = 0.50

            att.morning_delay_minutes = delay_min
            if delay_min > 0:
                hours = delay_min // 60
                mins = delay_min % 60
                att.delay_display = f"متأخر ({hours:02d}:{mins:02d})"
            elif att.check_in:
                att.delay_display = "على الموعد"
            else:
                att.delay_display = "-"

            att.late_deduction = late_ded

            # 3. Missing checkout deduction (Midnight rule)
            missing_ded = 0.0
            occ_rate = 100.0
            note = ""
            if not att.check_out and att.check_in:
                in_dt = att.check_in
                if not getattr(in_dt, 'tzinfo', None):
                    in_dt = pytz.utc.localize(in_dt)
                in_local = in_dt.astimezone(riyadh_tz)

                if in_local.date() < today_local_date:
                    missing_ded = 0.25  # Quarter day deduction
                    occ_rate = 0.0
                    note = "لم يسجل انصراف (خصم ربع يوم)"
                else:
                    note = "مفتوح حالياً"
            elif att.check_out:
                occ_rate = 100.0
                note = "مكتمل"

            att.missing_checkout_deduction = missing_ded
            att.net_deduction = round(late_ded + missing_ded, 2)
            att.occupancy_rate = occ_rate
            att.attendance_notes = note
