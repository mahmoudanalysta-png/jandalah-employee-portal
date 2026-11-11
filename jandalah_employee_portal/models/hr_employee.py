# -*- coding: utf-8 -*-
import math
import datetime
import logging
import pytz
from odoo import models, fields, api, exceptions, _

_logger = logging.getLogger(__name__)

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
            if getattr(attendance, 'is_leave_day', False):
                continue
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

    # --- Leave & Permission Tracking Fields ---
    is_leave_day = fields.Boolean(
        string='يوم إجازة',
        default=False,
        store=True
    )

    leave_id = fields.Many2one(
        'hr.leave',
        string='طلب الإجازة / الإذن المرتبط',
        store=True
    )

    leave_permission_type = fields.Selection([
        ('leave', 'إجازة'),
        ('permission', 'إذن'),
        ('none', '-'),
    ], string='نوع الإجازة / الإذن', compute='_compute_jehat_report_fields', store=True)

    leave_permission_display = fields.Char(
        string='إجازة / إذن',
        compute='_compute_jehat_report_fields',
        store=True
    )

    # --- Jehat Comprehensive Attendance Report Fields (Matching 17 Columns Blueprint) ---
    attendance_date = fields.Date(
        string='التاريخ',
        compute='_compute_jehat_report_fields',
        store=True
    )

    check_in_time = fields.Char(
        string='وقت الحضور',
        compute='_compute_jehat_report_fields',
        store=True
    )

    check_out_time = fields.Char(
        string='وقت الانصراف',
        compute='_compute_jehat_report_fields',
        store=True
    )

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

    break_duration = fields.Char(
        string='الاستراحة',
        default='-'
    )

    overtime_display = fields.Char(
        string='وقت إضافي',
        compute='_compute_jehat_report_fields',
        store=True
    )

    morning_delay_minutes = fields.Integer(
        string='دقائق التأخير',
        compute='_compute_jehat_report_fields',
        store=True
    )

    delay_display = fields.Char(
        string='تأخير',
        compute='_compute_jehat_report_fields',
        store=True
    )

    absence_deduction = fields.Float(
        string='خصم الغياب',
        default=0.0,
        digits=(10, 2)
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

    other_deductions = fields.Float(
        string='خصومات أخرى',
        default=0.0,
        digits=(10, 2)
    )

    bonus_amount = fields.Float(
        string='مكافأة',
        default=0.0,
        digits=(10, 2)
    )

    net_deduction = fields.Float(
        string='صافي',
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

    @api.depends('check_in', 'check_out', 'employee_id', 'is_outside_geofence', 'worked_hours', 'is_leave_day', 'leave_id')
    def _compute_jehat_report_fields(self):
        for att in self:
            # 1. Determine local employee timezone
            tz_name = 'Asia/Riyadh'
            if att.employee_id and att.employee_id.tz:
                tz_name = att.employee_id.tz
            elif att.employee_id and att.employee_id.user_id and att.employee_id.user_id.tz:
                tz_name = att.employee_id.user_id.tz
            elif self.env.user and self.env.user.tz:
                tz_name = self.env.user.tz

            try:
                emp_tz = pytz.timezone(tz_name)
            except Exception:
                emp_tz = pytz.timezone('Asia/Riyadh')

            now_utc = datetime.datetime.utcnow()
            now_local = pytz.utc.localize(now_utc).astimezone(emp_tz)
            today_local_date = now_local.date()

            # Handle full-day approved leave record: Blank punches, zero deductions, 100% occupancy
            if att.is_leave_day:
                if att.check_in:
                    in_dt = att.check_in
                    if not getattr(in_dt, 'tzinfo', None):
                        in_dt = pytz.utc.localize(in_dt)
                    in_local = in_dt.astimezone(emp_tz)
                    att.attendance_date = in_local.date()
                att.check_in_time = False
                att.check_out_time = False
                att.in_location_state = 'none'
                att.out_location_state = 'none'
                att.break_duration = '-'
                att.overtime_display = '-'
                att.morning_delay_minutes = 0
                att.delay_display = '-'
                att.absence_deduction = 0.0
                att.late_deduction = 0.0
                att.missing_checkout_deduction = 0.0
                att.other_deductions = 0.0
                att.bonus_amount = 0.0
                att.net_deduction = 0.0
                att.occupancy_rate = 100.0
                leave_name = att.leave_id.holiday_status_id.name if att.leave_id and att.leave_id.holiday_status_id else 'إجازة معتمدة'
                att.leave_permission_type = 'leave'
                att.leave_permission_display = f"إجازة ({leave_name})"
                att.attendance_notes = f"إجازة معتمدة ({leave_name})"
                continue

            # 2. Date, Time and Location states
            in_local = None
            if att.check_in:
                in_dt = att.check_in
                if not getattr(in_dt, 'tzinfo', None):
                    in_dt = pytz.utc.localize(in_dt)
                in_local = in_dt.astimezone(emp_tz)
                att.attendance_date = in_local.date()
                att.check_in_time = in_local.strftime('%I:%M:%S %p')
            else:
                att.attendance_date = False
                att.check_in_time = False

            out_local = None
            if att.check_out:
                out_dt = att.check_out
                if not getattr(out_dt, 'tzinfo', None):
                    out_dt = pytz.utc.localize(out_dt)
                out_local = out_dt.astimezone(emp_tz)
                att.check_out_time = out_local.strftime('%I:%M:%S %p')
            else:
                att.check_out_time = False

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

            # 3. Dynamic work schedule from employee contract or calendar
            # Grace period: 19 minutes
            work_start_min = 8 * 60  # Default: 8:00 AM
            standard_day_hours = 8.0
            emp = att.employee_id
            if emp:
                calendar = None
                contract = getattr(emp, 'contract_id', None)
                if contract and contract.resource_calendar_id:
                    calendar = contract.resource_calendar_id
                elif hasattr(emp, 'contract_ids'):
                    running_contracts = emp.contract_ids.filtered(lambda c: c.state == 'open' and c.resource_calendar_id)
                    if running_contracts:
                        calendar = running_contracts[0].resource_calendar_id
                    else:
                        with_cal = emp.contract_ids.filtered(lambda c: c.resource_calendar_id)
                        if with_cal:
                            calendar = with_cal[0].resource_calendar_id

                if not calendar and emp.resource_calendar_id:
                    calendar = emp.resource_calendar_id
                elif not calendar and emp.company_id and emp.company_id.resource_calendar_id:
                    calendar = emp.company_id.resource_calendar_id

                if calendar and calendar.attendance_ids and in_local:
                    weekday_str = str(in_local.weekday())
                    day_sched = calendar.attendance_ids.filtered(lambda s: s.dayofweek == weekday_str)
                    if not day_sched:
                        day_sched = calendar.attendance_ids
                    if day_sched:
                        min_start = min(day_sched.mapped('hour_from'))
                        work_start_min = int(round(min_start * 60))
                        tot_hours = sum(s.hour_to - s.hour_from for s in day_sched)
                        if tot_hours > 0:
                            standard_day_hours = tot_hours

            delay_min = 0
            late_ded = 0.0
            if in_local:
                punch_min = in_local.hour * 60 + in_local.minute
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
            elif in_local:
                att.delay_display = "على الموعد"
            else:
                att.delay_display = "-"

            att.late_deduction = late_ded

            # 4. Overtime calculation
            # Compare worked hours with dynamic standard hours
            if att.worked_hours and att.check_out:
                diff = att.worked_hours - standard_day_hours
                if diff > 0.02:
                    h = int(diff)
                    m = int(round((diff - h) * 60))
                    att.overtime_display = f"{h:02d}:{m:02d} +"
                elif diff < -0.02:
                    diff_abs = abs(diff)
                    h = int(diff_abs)
                    m = int(round((diff_abs - h) * 60))
                    att.overtime_display = f"{h:02d}:{m:02d} -"
                else:
                    att.overtime_display = "00:00 +"
            else:
                att.overtime_display = "-"

            # 5. Missing checkout deduction (Midnight rule)
            missing_ded = 0.0
            occ_rate = 100.0
            note = ""
            if not att.check_out and att.check_in and in_local:
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

            # 6. Check for approved leave or permission on this date
            att_date = att.attendance_date or (in_local.date() if in_local else None)
            leave_perm_type = 'none'
            leave_perm_disp = '-'
            if att_date and att.employee_id:
                domain = [
                    ('employee_id', '=', att.employee_id.id),
                    ('state', '=', 'validate'),
                    ('request_date_from', '<=', att_date),
                    ('request_date_to', '>=', att_date),
                ]
                approved_leave = self.env['hr.leave'].sudo().search(domain, limit=1)
                if approved_leave:
                    type_name = approved_leave.holiday_status_id.name or 'إجازة'
                    is_permission = (
                        getattr(approved_leave, 'request_unit_hours', False) or
                        (hasattr(approved_leave, 'number_of_hours_display') and approved_leave.number_of_hours_display > 0 and approved_leave.number_of_hours_display < 8) or
                        any(k in type_name for k in ['إذن', 'اذن', 'استئذان', 'ساعي', 'خروج'])
                    )
                    if is_permission:
                        leave_perm_type = 'permission'
                        hours = getattr(approved_leave, 'number_of_hours_display', 0)
                        if hours and hours > 0:
                            h_int = int(hours)
                            m_int = int(round((hours - h_int) * 60))
                            time_str = f"{h_int}:{m_int:02d} س" if m_int else f"{h_int} س"
                            leave_perm_disp = f"إذن ({time_str})"
                        else:
                            leave_perm_disp = f"إذن ({type_name})"

                        if not att.attendance_notes or att.attendance_notes in ['مكتمل', 'مفتوح حالياً']:
                            att.attendance_notes = f"إذن معتمد ({type_name})"
                        else:
                            att.attendance_notes = f"{att.attendance_notes} | إذن ({type_name})"
                    else:
                        leave_perm_type = 'leave'
                        leave_perm_disp = f"إجازة ({type_name})"

            att.leave_permission_type = leave_perm_type
            att.leave_permission_display = leave_perm_disp

    @api.model
    def sync_all_validated_leaves(self):
        """Batch generates attendance records for all existing validated leaves in DB and recalculates dynamic schedule."""
        leaves = self.env['hr.leave'].sudo().search([('state', '=', 'validate')])
        leaves._sync_leaves_to_attendance()
        all_att = self.sudo().search([('check_in', '!=', False)])
        if all_att:
            all_att._compute_jehat_report_fields()
        return True


class HrLeave(models.Model):
    _inherit = 'hr.leave'

    def action_validate(self):
        res = super(HrLeave, self).action_validate()
        try:
            self._sync_leaves_to_attendance()
        except Exception as e:
            _logger.exception("Error syncing leaves to attendance in action_validate: %s", e)
        return res

    def action_refuse(self):
        res = super(HrLeave, self).action_refuse()
        try:
            Attendance = self.env['hr.attendance'].sudo()
            for leave in self:
                leave_attendances = Attendance.search([
                    ('leave_id', '=', leave.id),
                    ('is_leave_day', '=', True),
                ])
                if leave_attendances:
                    leave_attendances.unlink()
        except Exception as e:
            _logger.exception("Error deleting attendance records in action_refuse: %s", e)
        return res

    def _sync_leaves_to_attendance(self):
        try:
            Attendance = self.env['hr.attendance'].sudo()
            for leave in self:
                if leave.state != 'validate' or not leave.employee_id:
                    continue

                type_name = leave.holiday_status_id.name or ''
                is_permission = (
                    getattr(leave, 'request_unit_hours', False) or
                    (hasattr(leave, 'number_of_hours_display') and leave.number_of_hours_display > 0 and leave.number_of_hours_display < 8) or
                    any(k in type_name for k in ['إذن', 'اذن', 'استئذان', 'ساعي', 'خروج'])
                )
                # Hourly permissions don't generate full-day blank attendance rows
                if is_permission:
                    continue

                date_from = leave.request_date_from or (leave.date_from.date() if leave.date_from else None)
                date_to = leave.request_date_to or (leave.date_to.date() if leave.date_to else None)
                if not date_from or not date_to:
                    continue

                cur_date = date_from
                while cur_date <= date_to:
                    dt_start = datetime.datetime.combine(cur_date, datetime.time(0, 0))
                    dt_end = datetime.datetime.combine(cur_date, datetime.time(23, 59, 59))
                    existing = Attendance.search([
                        ('employee_id', '=', leave.employee_id.id),
                        '|',
                        ('attendance_date', '=', cur_date),
                        '&', ('check_in', '>=', dt_start), ('check_in', '<=', dt_end),
                    ], limit=1)

                    if not existing:
                        try:
                            Attendance.create({
                                'employee_id': leave.employee_id.id,
                                'check_in': datetime.datetime.combine(cur_date, datetime.time(8, 0)),
                                'check_out': datetime.datetime.combine(cur_date, datetime.time(8, 0)),
                                'is_leave_day': True,
                                'leave_id': leave.id,
                            })
                        except Exception as ce:
                            _logger.warning("Could not create attendance row for leave day %s: %s", cur_date, ce)
                    elif existing and existing.is_leave_day and not existing.leave_id:
                        try:
                            existing.write({
                                'leave_id': leave.id,
                            })
                        except Exception as we:
                            _logger.warning("Could not update leave_id on attendance %s: %s", existing.id, we)

                    cur_date += datetime.timedelta(days=1)
        except Exception as e:
            _logger.exception("Error in _sync_leaves_to_attendance: %s", e)
