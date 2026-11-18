# -*- coding: utf-8 -*-
from odoo import http, fields, _, SUPERUSER_ID
from odoo.http import request
from odoo.addons.portal.controllers.portal import CustomerPortal, pager as portal_pager
import json
import pytz
import base64
import urllib.parse
from datetime import datetime, timedelta, date
import xmlrpc.client
import logging

_logger = logging.getLogger(__name__)

# Enterprise Sync Configuration
ENTERPRISE_URL = 'http://127.0.0.1:8071'
ENTERPRISE_DB = 'jandalah_dev'
ENTERPRISE_USER = 'admin'
ENTERPRISE_PWD = 'admin'

def get_current_user_tz(tz_name=None):
    """Dynamically resolves the current employee/user timezone from request cookie or user profile."""
    if tz_name:
        try:
            pytz.timezone(tz_name)
            return tz_name
        except Exception:
            pass

    # 1. Check client browser timezone cookie
    if request and hasattr(request, 'httprequest'):
        try:
            cookie_tz = request.httprequest.cookies.get('user_tz')
            if cookie_tz:
                pytz.timezone(cookie_tz)
                return cookie_tz
        except Exception:
            pass

    # 2. Check user profile in Odoo
    if request and hasattr(request, 'env') and request.env.user and request.env.user.tz:
        try:
            pytz.timezone(request.env.user.tz)
            return request.env.user.tz
        except Exception:
            pass

    # 3. Default fallback to Saudi Arabia
    return 'Asia/Riyadh'

def format_tz_dynamic(dt, tz_name=None, time_only=False):
    """Converts naive or UTC datetime to user's dynamic local timezone and formats cleanly."""
    if not dt:
        return '-'
    if isinstance(dt, str):
        try:
            dt = fields.Datetime.from_string(dt)
        except Exception:
            return dt

    resolved_tz = get_current_user_tz(tz_name)
    local_dt = None
    try:
        user_tz = pytz.timezone(resolved_tz)
        if not getattr(dt, 'tzinfo', None):
            dt = pytz.utc.localize(dt)
        local_dt = dt.astimezone(user_tz)
    except Exception:
        pass

    if local_dt is None:
        try:
            # Fallback calculation if timezone lookup fails
            local_dt = dt + timedelta(hours=3)
        except Exception:
            local_dt = dt

    try:
        am_pm = 'صباحاً' if local_dt.strftime('%p') == 'AM' else 'مساءً'
        if time_only:
            return f"{local_dt.strftime('%I:%M:%S')} {am_pm}"
        return f"{local_dt.strftime('%Y/%m/%d %I:%M')} {am_pm}"
    except Exception:
        if time_only:
            return local_dt.strftime('%I:%M:%S %p')
        return local_dt.strftime('%Y/%m/%d %I:%M %p')

format_tz_saudi = format_tz_dynamic

def sync_attendance_to_enterprise(employee_name, employee_email, employee_barcode, action, check_time, lat=None, lon=None, ip=None, browser=None):
    # Syncs attendance punch to Enterprise instance via XML-RPC API
    try:
        common = xmlrpc.client.ServerProxy(f"{ENTERPRISE_URL}/xmlrpc/2/common")
        uid = common.authenticate(ENTERPRISE_DB, ENTERPRISE_USER, ENTERPRISE_PWD, {})
        if not uid:
            _logger.warning("Failed to authenticate to Enterprise instance for attendance sync.")
            return False

        models = xmlrpc.client.ServerProxy(f"{ENTERPRISE_URL}/xmlrpc/2/object")
        
        # 1. Match or create employee in Enterprise
        ent_emp_id = None
        if employee_barcode:
            emps = models.execute_kw(ENTERPRISE_DB, uid, ENTERPRISE_PWD, 'hr.employee', 'search', [[('barcode', '=', employee_barcode)]])
            if emps:
                ent_emp_id = emps[0]
        if not ent_emp_id and employee_email:
            emps = models.execute_kw(ENTERPRISE_DB, uid, ENTERPRISE_PWD, 'hr.employee', 'search', [[('work_email', '=ilike', employee_email.strip())]])
            if emps:
                ent_emp_id = emps[0]
        if not ent_emp_id and employee_name:
            emps = models.execute_kw(ENTERPRISE_DB, uid, ENTERPRISE_PWD, 'hr.employee', 'search', [[('name', 'ilike', employee_name.strip())]])
            if emps:
                ent_emp_id = emps[0]

        if not ent_emp_id:
            # Create employee in Enterprise if not existing
            ent_emp_id = models.execute_kw(ENTERPRISE_DB, uid, ENTERPRISE_PWD, 'hr.employee', 'create', [{
                'name': employee_name,
                'work_email': employee_email or '',
                'barcode': employee_barcode or False,
            }])

        time_str = check_time.strftime('%Y-%m-%d %H:%M:%S') if hasattr(check_time, 'strftime') else str(check_time)

        # 2. Sync Check-In
        if action == 'checked_in':
            vals = {
                'employee_id': ent_emp_id,
                'check_in': time_str,
                'in_ip_address': ip or 'N/A',
                'in_browser': browser or 'Mobile Community Portal',
            }
            if lat and lon:
                try:
                    vals['in_latitude'] = float(lat)
                    vals['in_longitude'] = float(lon)
                except (ValueError, TypeError):
                    pass
            att_id = models.execute_kw(ENTERPRISE_DB, uid, ENTERPRISE_PWD, 'hr.attendance', 'create', [vals])
            _logger.info(f"Successfully synced Check-In to Enterprise. Attendance ID: {att_id}")
            return att_id

        # 3. Sync Check-Out
        elif action == 'checked_out':
            open_atts = models.execute_kw(ENTERPRISE_DB, uid, ENTERPRISE_PWD, 'hr.attendance', 'search', [[
                ('employee_id', '=', ent_emp_id),
                ('check_out', '=', False)
            ]], {'order': 'check_in desc', 'limit': 1})

            if open_atts:
                vals = {
                    'check_out': time_str,
                    'out_ip_address': ip or 'N/A',
                    'out_browser': browser or 'Mobile Community Portal',
                }
                if lat and lon:
                    try:
                        vals['out_latitude'] = float(lat)
                        vals['out_longitude'] = float(lon)
                    except (ValueError, TypeError):
                        pass
                models.execute_kw(ENTERPRISE_DB, uid, ENTERPRISE_PWD, 'hr.attendance', 'write', [[open_atts[0]], vals])
                _logger.info(f"Successfully synced Check-Out to Enterprise. Attendance ID: {open_atts[0]}")
                return open_atts[0]
            else:
                _logger.warning("No open attendance in Enterprise to check-out.")
    except Exception as e:
        _logger.error(f"Error during Enterprise sync: {e}")
        return False


class JandalahCustomerPortal(CustomerPortal):

    def _get_employee(self):
        # Helper to find the employee record linked to current user with comprehensive fallbacks & strict company isolation
        user = request.env.user
        company = request.env.company
        # 1. Search in current active company first
        emp = request.env['hr.employee'].sudo().search([
            ('user_id', '=', user.id),
            ('company_id', '=', company.id)
        ], order='id desc', limit=1)

        # 2. If not found in current company, search across all companies
        if not emp:
            emp = request.env['hr.employee'].sudo().search([('user_id', '=', user.id)], order='id desc', limit=1)

        # 3. If still not found, search by email/login in current company first
        if not emp:
            user_email = (user.email or '').strip()
            user_login = (user.login or '').strip()
            domain = ['|', '|', '|',
                ('work_email', '=ilike', user_email),
                ('work_email', '=ilike', user_login),
                ('private_email', '=ilike', user_email),
                ('work_contact_id', '=', user.partner_id.id),
            ]
            emp = request.env['hr.employee'].sudo().search([('company_id', '=', company.id)] + domain, order='id desc', limit=1)
            if not emp:
                emp = request.env['hr.employee'].sudo().search(domain, order='id desc', limit=1)

            if emp and not emp.user_id:
                existing = request.env['hr.employee'].sudo().search_count([
                    ('user_id', '=', user.id),
                    ('company_id', '=', emp.company_id.id)
                ])
                if existing == 0:
                    try:
                        with request.env.cr.savepoint():
                            emp.sudo().write({'user_id': user.id})
                    except Exception as e:
                        _logger.warning("Could not auto-link user_id to employee %s: %s", emp.id, e)

        # Dynamically sync detected timezone with safety savepoints (prevents constraint rollbacks)
        if emp:
            try:
                detected_tz = get_current_user_tz()
                if detected_tz:
                    if emp.tz != detected_tz:
                        try:
                            with request.env.cr.savepoint():
                                emp.sudo().write({'tz': detected_tz})
                        except Exception:
                            pass
                    if user and user.tz != detected_tz:
                        try:
                            with request.env.cr.savepoint():
                                user.sudo().write({'tz': detected_tz})
                        except Exception:
                            pass
            except Exception as e:
                _logger.warning("Could not sync timezone to employee %s: %s", emp.id, e)

        return emp

    def _get_subordinates(self, emp):
        """Finds all subordinates where the current employee is Direct Manager (parent_id) or Coach (coach_id),
        with smart executive/HR admin fallback so company leaders can approve team leaves seamlessly."""
        if not emp:
            return request.env['hr.employee'].sudo().browse()
        subs = request.env['hr.employee'].sudo().search([
            '|', ('parent_id', '=', emp.id), ('coach_id', '=', emp.id)
        ])
        if not subs:
            user = request.env.user
            is_hr_admin = (
                user.has_group('hr_holidays.group_hr_holidays_user') or
                user.has_group('hr_holidays.group_hr_holidays_manager') or
                user.has_group('base.group_system') or
                user._is_admin()
            )
            if is_hr_admin:
                subs = request.env['hr.employee'].sudo().search([
                    ('company_id', '=', request.env.company.id),
                    ('id', '!=', emp.id)
                ])
        return subs

    def _get_employee_leave_balances(self, emp):
        """Computes strict day-based leave balances for regular and casual leaves, plus monthly permission hours."""
        res = {
            'balances': [],
            'monthly_perm_used': 0.0,
            'monthly_perm_remaining': 9.0,
            'monthly_perm_text': '9 ساعات',
            'is_in_probation': False,
            'probation_end_date': '',
        }
        if not emp:
            return res

        try:
            today_d = date.today()
            start_date = getattr(emp, 'first_contract_date', False) or (emp.contract_id.date_start if getattr(emp, 'contract_id', False) else False)
            is_in_probation = False
            probation_end_date = None
            if start_date:
                from dateutil.relativedelta import relativedelta
                six_months_date = start_date + relativedelta(months=6)
                probation_end_date = six_months_date
                if today_d < six_months_date:
                    is_in_probation = True

            res['is_in_probation'] = is_in_probation
            res['probation_end_date'] = str(probation_end_date) if probation_end_date else ''

            LeaveType = request.env['hr.leave.type'].sudo()
            LeaveAlloc = request.env['hr.leave.allocation'].sudo()
            LeaveReq = request.env['hr.leave'].sudo()

            # 1. Ensure standard Leave Types exist:
            for type_name in ['إجازة اعتيادية', 'إجازة عارضة']:
                lt = LeaveType.search([('name', '=', type_name)], limit=1)
                if not lt:
                    try:
                        LeaveType.create({
                            'name': type_name,
                            'requires_allocation': 'yes',
                            'leave_validation_type': 'both',
                            'request_unit': 'day',
                        })
                    except Exception as e_cr:
                        _logger.warning("Could not auto-create leave type %s: %s", type_name, e_cr)

            # Ensure 'إذن' exists for hourly permissions
            perm_type = LeaveType.search([('name', '=', 'إذن')], limit=1)
            if not perm_type:
                perm_type = LeaveType.search([('name', 'ilike', 'إذن')], limit=1)
            if not perm_type:
                try:
                    p_vals = {
                        'name': 'إذن',
                        'requires_allocation': 'no',
                        'leave_validation_type': 'both',
                    }
                    if 'request_unit' in LeaveType._fields:
                        p_vals['request_unit'] = 'hour'
                    LeaveType.create(p_vals)
                except Exception as e_perm:
                    _logger.warning("Could not auto-create permission type: %s", e_perm)

            # 2. Query leave types and compute days
            all_types = LeaveType.search([('active', '=', True)], order='sequence asc, id asc')
            balances = []
            for lt in all_types:
                # Exclude purely hourly permissions
                is_perm = any(k in (lt.name or '') for k in ['إذن', 'اذن', 'استئذان', 'ساعي', 'خروج'])
                if is_perm:
                    continue

                # Sum approved allocations
                alloc_records = LeaveAlloc.search([
                    ('employee_id', '=', emp.id),
                    ('holiday_status_id', '=', lt.id),
                    ('state', '=', 'validate')
                ])
                total_allocated = sum(alloc_records.mapped('number_of_days'))

                # Sum confirmed / validated leaves taken
                taken_records = LeaveReq.search([
                    ('employee_id', '=', emp.id),
                    ('holiday_status_id', '=', lt.id),
                    ('state', 'in', ['validate', 'validate1', 'confirm'])
                ])
                total_taken = sum(taken_records.mapped('number_of_days'))

                # Skip unpaid / generic demo types if they have 0 allocation
                if total_allocated == 0 and any(k in (lt.name or '') for k in ['غير مدفوعة', 'بدون راتب', 'Unpaid']):
                    continue

                remaining = max(0.0, total_allocated - total_taken)
                rem_val = int(remaining) if remaining.is_integer() else round(remaining, 1)
                max_val = int(total_allocated) if total_allocated.is_integer() else round(total_allocated, 1)

                unit_str = "أيام" if (3 <= rem_val <= 10) else "يوم"
                remaining_text = f"{rem_val} {unit_str}"

                balances.append({
                    'id': lt.id,
                    'name': lt.name,
                    'remaining': rem_val,
                    'remaining_text': remaining_text,
                    'max': max_val,
                    'unlimited': False,
                    'color': 'success' if remaining >= 5 else ('warning' if remaining > 0 else 'danger'),
                })
            res['balances'] = balances

            # 3. Compute current month's permission hours (Limit: 9 hours per month)
            today_d = date.today()
            m_start = today_d.replace(day=1)
            if today_d.month == 12:
                m_end = today_d.replace(year=today_d.year + 1, month=1, day=1) - timedelta(days=1)
            else:
                m_end = today_d.replace(month=today_d.month + 1, day=1) - timedelta(days=1)

            month_leaves = LeaveReq.search([
                ('employee_id', '=', emp.id),
                ('state', 'in', ['confirm', 'validate1', 'validate']),
                ('request_date_from', '>=', str(m_start)),
                ('request_date_from', '<=', str(m_end)),
            ])
            used_h = 0.0
            for ml in month_leaves:
                m_tname = ml.holiday_status_id.name or ''
                if (getattr(ml, 'request_unit_hours', False) or
                    (hasattr(ml, 'number_of_hours_display') and ml.number_of_hours_display > 0 and ml.number_of_hours_display < 8) or
                    any(k in m_tname for k in ['إذن', 'اذن', 'استئذان', 'ساعي', 'خروج'])):
                    h_val = getattr(ml, 'number_of_hours_display', 0.0) or (ml.number_of_days * 8.0 if ml.number_of_days < 1 else 3.0)
                    used_h += h_val

            rem_h = max(0.0, 9.0 - used_h)
            rem_h_disp = int(rem_h) if rem_h.is_integer() else round(rem_h, 1)
            used_h_disp = int(used_h) if used_h.is_integer() else round(used_h, 1)
            res['monthly_perm_used'] = used_h_disp
            res['monthly_perm_remaining'] = rem_h_disp
            res['monthly_perm_text'] = f"{rem_h_disp} س"

            # Count of permission requests in current month (max 3 per month)
            perm_count = sum(
                1 for ml in month_leaves
                if (getattr(ml, 'request_unit_hours', False) or
                    (hasattr(ml, 'number_of_hours_display') and ml.number_of_hours_display > 0 and ml.number_of_hours_display < 8) or
                    any(k in (ml.holiday_status_id.name or '') for k in ['إذن', 'اذن', 'استئذان', 'ساعي', 'خروج']))
            )
            res['monthly_perm_count_used'] = perm_count
            res['monthly_perm_count_remaining'] = max(0, 3 - perm_count)
            res['monthly_perm_count_text'] = f"{max(0, 3 - perm_count)} من 3 أذونات"
            res['is_in_probation'] = is_in_probation
            res['probation_end_date'] = str(probation_end_date) if probation_end_date else None
        except Exception as e:
            _logger.warning("Error in _get_employee_leave_balances: %s", e)

        return res

    def _get_jandalah_dashboard_values(self, emp=None):
        """Prepares all employee dashboard, attendance, notifications, and supervisor values for /my."""
        values = {}
        if not emp:
            emp = self._get_employee()
        if not emp:
            return values

        user_tz_name = get_current_user_tz()
        user_tz = pytz.timezone(user_tz_name)
        now_local = pytz.utc.localize(datetime.utcnow()).astimezone(user_tz)
        today_local_date = now_local.date()

        open_att = request.env['hr.attendance'].sudo().search([
            ('employee_id', '=', emp.id),
            ('check_out', '=', False)
        ], order='check_in desc', limit=1)

        # Enforce Midnight Rule: If open attendance is from prior date, it is expired
        is_checked_in = False
        if open_att and open_att.check_in:
            check_in_local = pytz.utc.localize(open_att.check_in).astimezone(user_tz)
            if check_in_local.date() == today_local_date:
                is_checked_in = True
            else:
                open_att = False
        
        last_att = request.env['hr.attendance'].sudo().search([
            ('employee_id', '=', emp.id)
        ], order='check_in desc', limit=1)

        # Smart Overdue Checkout Alert (Shift completion reminder)
        is_overdue_checkout = False
        overdue_checkout_msg = ''
        if is_checked_in and open_att:
            try:
                calendar = None
                emp_sudo = emp.sudo() if emp else False
                contract = getattr(emp_sudo, 'contract_id', None) if emp_sudo else None
                if contract and contract.sudo().resource_calendar_id:
                    calendar = contract.sudo().resource_calendar_id
                elif hasattr(emp_sudo, 'contract_ids'):
                    running = emp_sudo.contract_ids.filtered(lambda c: c.state == 'open' and c.resource_calendar_id)
                    if running:
                        calendar = running[0].sudo().resource_calendar_id
                    elif emp_sudo.contract_ids:
                        with_cal = emp_sudo.contract_ids.filtered(lambda c: c.resource_calendar_id)
                        if with_cal:
                            calendar = with_cal[0].sudo().resource_calendar_id
                if not calendar and emp_sudo and emp_sudo.resource_calendar_id:
                    calendar = emp_sudo.resource_calendar_id
                elif not calendar and emp_sudo and emp_sudo.company_id and emp_sudo.company_id.sudo().resource_calendar_id:
                    calendar = emp_sudo.company_id.sudo().resource_calendar_id

                shift_hours = calendar.sudo().hours_per_day if calendar and calendar.sudo().hours_per_day else 8.0
                if open_att.check_in:
                    check_in_dt = pytz.utc.localize(open_att.check_in).astimezone(user_tz)
                    elapsed_hours = (now_local - check_in_dt).total_seconds() / 3600.0

                    # 1. Check scheduled calendar attendance for today (e.g. shift ends at 16:00 / 4:00 PM)
                    scheduled_end_dt = None
                    if calendar and calendar.sudo().attendance_ids:
                        today_dow = str(now_local.weekday())
                        today_atts = calendar.sudo().attendance_ids.filtered(lambda a: a.dayofweek == today_dow)
                        if today_atts:
                            max_hour_to = max(today_atts.mapped('hour_to'))
                            h_end = int(max_hour_to)
                            m_end = int(round((max_hour_to - h_end) * 60))
                            scheduled_end_dt = now_local.replace(hour=h_end, minute=m_end, second=0, microsecond=0)

                    # 2. Determine overdue condition:
                    if scheduled_end_dt:
                        # Shift ends at scheduled time; allow a 15-minute grace period before reminding
                        if now_local >= (scheduled_end_dt + timedelta(minutes=15)):
                            is_overdue_checkout = True
                            over_mins = int((now_local - scheduled_end_dt).total_seconds() / 60)
                            end_time_str = scheduled_end_dt.strftime('%I:%M %p').replace('AM', 'ص').replace('PM', 'م')
                            overdue_checkout_msg = f'انتهى موعد دوامك الرسمي المقرر (الساعة {end_time_str}) منذ {over_mins} دقيقة. يرجى تسجيل الانصراف الآن!'
                    else:
                        # Fallback if no calendar schedule: require elapsed hours >= 8 hours + 30 mins buffer
                        effective_shift = max(8.0, shift_hours)
                        if elapsed_hours >= (effective_shift + 0.5):
                            is_overdue_checkout = True
                            over_mins = int((elapsed_hours - effective_shift) * 60)
                            overdue_checkout_msg = f'انتهت ساعات دوامك المقررة ({int(effective_shift)} ساعات) منذ {over_mins} دقيقة. يرجى تسجيل الانصراف الآن!'
            except Exception as e_cal:
                _logger.warning("Error checking overdue checkout: %s", e_cal)

        # Supervisor / Team Attendance Real-Time Stats
        subordinates = self._get_subordinates(emp)
        is_supervisor = bool(subordinates)
        team_checked_in = 0
        team_pending_leaves = 0
        if is_supervisor:
            team_checked_in = request.env['hr.attendance'].sudo().search_count([
                ('employee_id', 'in', subordinates.ids),
                ('check_out', '=', False)
            ])
            team_pending_leaves = request.env['hr.leave'].sudo().search_count([
                ('employee_id', 'in', subordinates.ids),
                ('state', 'in', ['confirm', 'validate1'])
            ])

        # Counts for announcements, assets, penalties, and location requests
        unread_announcements = 0
        pending_assets = 0
        pending_penalties = 0
        pending_locations = 0

        try:
            # Announcements visible to this employee without ack
            Ann = request.env['jandalah.announcement'].sudo()
            Ack = request.env['jandalah.announcement.ack'].sudo()
            my_acked_ids = Ack.search([('employee_id', '=', emp.id)]).mapped('announcement_id.id')
            all_ann = Ann.search([('active', '=', True)])
            unread_announcements = sum(1 for a in all_ann if a.is_visible_for(emp) and a.id not in my_acked_ids)
        except Exception:
            pass

        try:
            # Assets assigned to this employee awaiting acknowledgment
            pending_assets = request.env['jandalah.hr.asset'].sudo().search_count([
                ('employee_id', '=', emp.id),
                ('state', '=', 'assigned')
            ])
        except Exception:
            pass

        try:
            # Penalties issued awaiting employee response
            pending_penalties = request.env['jandalah.hr.penalty'].sudo().search_count([
                ('employee_id', '=', emp.id),
                ('state', '=', 'issued')
            ])
        except Exception:
            pass

        try:
            # Active/pending location requests
            pending_locations = request.env['jandalah.location.request'].sudo().search_count([
                ('employee_id', '=', emp.id),
                ('state', '=', 'confirm')
            ])
        except Exception:
            pass

        # Check if this employee's job position is 'اخصائي مبيعات'
        is_sales_employee = False
        try:
            job_title_str = (emp.sudo().job_title or '').strip().lower()
            job_name_str = (emp.sudo().job_id and emp.sudo().job_id.name or '').strip().lower()
            sales_keywords = ['اخصائي مبيعات', 'أخصائي مبيعات', 'sales specialist']
            if any(kw in job_name_str or kw in job_title_str for kw in sales_keywords):
                is_sales_employee = True
        except Exception:
            pass

        # Recent Manager Approved Leaves (Within past 5 days)
        recent_approved_leaves_count = 0
        try:
            five_days_ago = fields.Datetime.to_string(datetime.utcnow() - timedelta(days=5))
            recent_approved_leaves_count = request.env['hr.leave'].sudo().search_count([
                ('employee_id', '=', emp.id),
                ('state', '=', 'validate'),
                ('write_date', '>=', five_days_ago)
            ])
        except Exception:
            pass

        # Leave Balances and Monthly Permission Stats (Strictly in DAYS & HOURS)
        leave_info = self._get_employee_leave_balances(emp)
        leave_balances = leave_info.get('balances', [])
        monthly_perm_used = leave_info.get('monthly_perm_used', 0.0)
        monthly_perm_remaining = leave_info.get('monthly_perm_remaining', 9.0)
        monthly_perm_text = leave_info.get('monthly_perm_text', '9 ساعات')
        monthly_perm_month_name = leave_info.get('monthly_perm_month_name', '')

        # Total notifications for header bell and central dashboard
        total_active_notifications = (
            unread_announcements +
            pending_assets +
            pending_penalties +
            (1 if is_overdue_checkout else 0) +
            (1 if recent_approved_leaves_count > 0 else 0) +
            (team_pending_leaves if is_supervisor else 0)
        )

        values.update({
            'employee': emp,
            'is_sales_employee': is_sales_employee,
            'is_checked_in': is_checked_in,
            'open_attendance': open_att,
            'last_attendance': last_att,
            'is_overdue_checkout': is_overdue_checkout,
            'overdue_checkout_msg': overdue_checkout_msg,
            'attendance_count': request.env['hr.attendance'].sudo().search_count([('employee_id', '=', emp.id)]),
            'leaves_count': request.env['hr.leave'].sudo().search_count([('employee_id', '=', emp.id)]),
            'recent_approved_leaves_count': recent_approved_leaves_count,
            'leave_balances': leave_balances,
            'is_in_probation': leave_info.get('is_in_probation', False),
            'probation_end_date': leave_info.get('probation_end_date', ''),
            'monthly_perm_used': monthly_perm_used,
            'monthly_perm_remaining': monthly_perm_remaining,
            'monthly_perm_text': monthly_perm_text,
            'monthly_perm_month_name': monthly_perm_month_name,
            'unread_announcements': unread_announcements,
            'pending_assets': pending_assets,
            'pending_penalties': pending_penalties,
            'pending_locations': pending_locations,
            'total_active_notifications': total_active_notifications,
            'format_tz': format_tz_dynamic,
            # Supervisor values
            'is_supervisor': is_supervisor,
            'subordinates_count': len(subordinates),
            'team_checked_in_count': team_checked_in,
            'team_pending_leaves_count': team_pending_leaves,
        })
        return values

    def _prepare_home_portal_values(self, counters):
        """
        Odoo 18 FIX:
        - HTTP page load: super() calls this with counters=[] (empty list) → bool([]) is False
          → we ADD jandalah ESS data → template renders correctly ✓
        - RPC /my/counters call: JS sends counters=['key1','key2',...] (non-empty list) → bool([...]) is True
          → we return ONLY super() values → no textContent crash ✓
        """
        values = super()._prepare_home_portal_values(counters)
        if counters and isinstance(counters, (list, tuple, set)):
            # RPC call — return only numeric counters, never ESS objects
            return values
        # HTTP page load — inject all jandalah dashboard data
        values.update(self._get_jandalah_dashboard_values())
        return values

    @http.route('/jandalah/manifest.json', type='http', auth='public', methods=['GET'], csrf=False)
    def portal_pwa_manifest(self, **kw):
        manifest_data = {
            "id": "/my",
            "name": "بوابة موظفي جندله",
            "short_name": "موظفي جندله",
            "start_url": "/my",
            "scope": "/my",
            "display": "standalone",
            "background_color": "#ffffff",
            "theme_color": "#0d6efd",
            "description": "بوابة الخدمة الذاتية للموظفين - تسجيل الحضور الذكي بالـ GPS والإجازات",
            "orientation": "portrait-primary",
            "icons": [
                {
                    "src": "/jandalah_employee_portal/static/src/img/icon-192.png",
                    "sizes": "192x192",
                    "type": "image/png",
                    "purpose": "any maskable"
                },
                {
                    "src": "/jandalah_employee_portal/static/src/img/icon-512.png",
                    "sizes": "512x512",
                    "type": "image/png",
                    "purpose": "any maskable"
                },
                {
                    "src": "/jandalah_employee_portal/static/src/img/icon-192.png",
                    "sizes": "96x96",
                    "type": "image/png",
                    "purpose": "badge"
                }
            ]
        }
        return request.make_response(
            json.dumps(manifest_data, ensure_ascii=False),
            headers=[
                ('Content-Type', 'application/manifest+json; charset=utf-8'),
                ('Cache-Control', 'public, max-age=3600'),
            ]
        )

    @http.route(['/sw.js', '/jandalah/sw.js'], type='http', auth='public', methods=['GET'], csrf=False)
    def portal_pwa_sw(self, **kw):
        sw_code = """
const CACHE_NAME = 'jandalah-ess-v3';

self.addEventListener('install', event => {
    self.skipWaiting();
});

self.addEventListener('activate', event => {
    event.waitUntil(self.clients.claim());
});

self.addEventListener('fetch', event => {
    event.respondWith(
        fetch(event.request).catch(() => caches.match(event.request))
    );
});

// Mobile Push Notifications (Like WhatsApp & Native Apps)
self.addEventListener('push', event => {
    let payload = {
        title: 'شركة جندله | بوابة الموظفين',
        body: 'لديك تنبيه رسمي جديد من إدارة الشركة',
        url: '/my',
        tag: 'jandalah-alert-' + Date.now()
    };
    try {
        if (event.data) {
            const raw = event.data.json();
            payload = Object.assign(payload, raw);
        }
    } catch(e) {
        if (event.data) {
            payload.body = event.data.text();
        }
    }

    const options = {
        body: payload.body,
        icon: '/jandalah_employee_portal/static/src/img/icon-192.png',
        badge: '/jandalah_employee_portal/static/src/img/icon-192.png',
        vibrate: [300, 100, 300, 100, 300],
        tag: payload.tag || 'jandalah-general',
        renotify: true,
        requireInteraction: false,
        data: { url: payload.url || '/my' },
        actions: [
            { action: 'open', title: 'فتح التطبيق' }
        ]
    };

    event.waitUntil(
        Promise.all([
            self.registration.showNotification(payload.title, options),
            ('setAppBadge' in self.navigator) ? self.navigator.setAppBadge().catch(() => {}) : Promise.resolve()
        ])
    );
});

self.addEventListener('notificationclick', event => {
    event.notification.close();
    if ('clearAppBadge' in self.navigator) {
        self.navigator.clearAppBadge().catch(() => {});
    }
    const targetUrl = (event.notification.data && event.notification.data.url) ? event.notification.data.url : '/my';
    event.waitUntil(
        clients.matchAll({ type: 'window', includeUncontrolled: true }).then(windowClients => {
            for (let client of windowClients) {
                if (client.url && client.url.includes('/my') && 'focus' in client) {
                    client.navigate(targetUrl);
                    return client.focus();
                }
            }
            if (clients.openWindow) {
                return clients.openWindow(targetUrl);
            }
        })
    );
});
"""
        return request.make_response(
            sw_code.strip(),
            headers=[
                ('Content-Type', 'application/javascript; charset=utf-8'),
                ('Service-Worker-Allowed', '/'),
                ('Cache-Control', 'no-cache'),
            ]
        )

    @http.route('/jandalah/vapid_key', type='json', auth='public', methods=['POST', 'GET'], csrf=False)
    def get_vapid_key(self, **kw):
        """Returns standard VAPID public key for WebPush client subscriptions."""
        return {
            'status': 'success',
            'public_key': 'BDEmktt-z8gbD_wFgEfK_1EZspp_W04tbpR7dtkT9Z1TVYZ6t71Q7F47LGYN8OOFhVnICBjLPYyvarRPfzg2ZiE'
        }

    @http.route('/my/push/subscribe', type='json', auth='user', methods=['POST'], csrf=False)
    def push_subscribe(self, endpoint=None, keys=None, device_name=None, raw_json=None, **kw):
        """Registers the employee mobile browser for real Web Push notifications."""
        emp = self._get_employee()
        if not emp or not endpoint:
            return {'status': 'error', 'message': 'بيانات الاشتراك غير مكتملة'}

        p256dh = keys.get('p256dh') if keys else None
        auth = keys.get('auth') if keys else None
        Subscriber = request.env['jandalah.push.subscriber'].sudo()
        sub = Subscriber.register_subscription(
            employee=emp,
            endpoint=endpoint,
            p256dh=p256dh,
            auth=auth,
            device_name=device_name or 'هاتف محمول',
            raw_json=raw_json
        )
        return {'status': 'success', 'subscriber_id': sub.id}

    @http.route('/my/push/test', type='json', auth='user', methods=['POST'], csrf=False)
    def push_test(self, **kw):
        """Triggers an instant push notification test for the logged-in employee."""
        emp = self._get_employee()
        if not emp:
            return {'status': 'error', 'message': 'لا يوجد ملف موظف مرتبط'}

        Subscriber = request.env['jandalah.push.subscriber'].sudo()
        sent_count = Subscriber.notify_employee(
            employee=emp,
            title='شركة جندله | تجربة الإشعارات 🔔',
            body=f'مرحباً {emp.name}، تم تفعيل إشعارات الهاتف المنبثقة بنجاح على جهازك!',
            url='/my'
        )
        return {'status': 'success', 'sent_count': sent_count}

    @http.route('/my/notifications/poll', type='json', auth='user', methods=['POST'], csrf=False)
    def notifications_poll(self, **kw):
        """
        Real-time poller for all Bell (مركز التنبيهات) events:
        1. Announcements & Decisions (تعاميم وقرارات إدارية)
        2. Penalties & Inquiries (جزاءات وتحقيقات إدارية)
        3. Assigned Assets (عهد عينية ورقمية بانتظار الإقرار)
        4. Approved Leaves (اعتماد إجازات الموظف)
        5. Team Leaves for Supervisors (طلبات إجازات الفريق بانتظار الاعتماد)
        6. Overdue Shift Checkout (تنبيه انتهاء الدوام والانصراف)
        """
        emp = self._get_employee()
        user = request.env.user
        alerts = []

        # 1. Announcements & Administrative Decisions
        try:
            Ann = request.env['jandalah.announcement'].sudo()
            Ack = request.env['jandalah.announcement.ack'].sudo()
            my_acked_ids = set()
            if emp:
                my_acked_ids = set(Ack.search([('employee_id', '=', emp.id)]).mapped('announcement_id.id'))
            all_ann = Ann.search([('active', '=', True)], order='date desc, id desc', limit=10)
            for a in all_ann:
                is_vis = a.is_visible_for(emp) if emp else (a.target_type == 'all')
                if is_vis and a.id not in my_acked_ids:
                    alerts.append({
                        'id': f'ann_{a.id}',
                        'type': 'announcement',
                        'title': f'تعميم إداري جديد: {a.name}',
                        'body': f'صادر عن: {a.issued_by or "الإدارة العامة"}. يرجى الاطلاع والإقرار.',
                        'url': '/my/announcements',
                        'tag': f'ann_{a.id}'
                    })
        except Exception as e_ann:
            _logger.warning("Error polling announcements: %s", e_ann)

        if emp:
            # 2. Pending Penalties
            try:
                Penalty = request.env['jandalah.hr.penalty'].sudo()
                pending_pens = Penalty.search([
                    ('employee_id', '=', emp.id),
                    ('state', '=', 'issued'),
                    ('employee_response', '=', False)
                ], limit=5)
                for p in pending_pens:
                    alerts.append({
                        'id': f'pen_{p.id}',
                        'type': 'penalty',
                        'title': 'تنبيه إداري رسمي ⚠️',
                        'body': f'لديك إجراء إداري بانتظار إفادتك: {p.name or p.penalty_type}',
                        'url': '/my/penalties',
                        'tag': f'pen_{p.id}'
                    })
            except Exception as e_pen:
                _logger.warning("Error polling penalties: %s", e_pen)

            # 3. Pending Assets
            try:
                Asset = request.env['jandalah.hr.asset'].sudo()
                pending_assets = Asset.search([
                    ('employee_id', '=', emp.id),
                    ('state', '=', 'assigned')
                ], limit=5)
                for ast in pending_assets:
                    alerts.append({
                        'id': f'asset_{ast.id}',
                        'type': 'asset',
                        'title': 'إقرار استلام عهدة 💻',
                        'body': f'تم تسليمك عهدة جديدة ({ast.name}). يرجى تأكيد استلامها.',
                        'url': '/my/assets',
                        'tag': f'asset_{ast.id}'
                    })
            except Exception as e_ast:
                _logger.warning("Error polling assets: %s", e_ast)

            # 4. Approved & Refused Leaves (Past 5 days)
            try:
                five_days_ago = fields.Datetime.to_string(datetime.utcnow() - timedelta(days=5))
                # 4.a Approved
                approved_leaves = request.env['hr.leave'].sudo().search([
                    ('employee_id', '=', emp.id),
                    ('state', '=', 'validate'),
                    ('write_date', '>=', five_days_ago)
                ], order='write_date desc', limit=3)
                for al in approved_leaves:
                    st_name = al.holiday_status_id.name if al.holiday_status_id else 'إجازة'
                    alerts.append({
                        'id': f'leave_appr_{al.id}',
                        'type': 'leave_approved',
                        'title': 'تمت الموافقة على طلب إجازتك ✅',
                        'body': f'تم اعتماد طلبك ({st_name}) بنجاح من قبل الإدارة.',
                        'url': '/my/leaves',
                        'tag': f'leave_appr_{al.id}'
                    })
                # 4.b Refused
                refused_leaves = request.env['hr.leave'].sudo().search([
                    ('employee_id', '=', emp.id),
                    ('state', '=', 'refuse'),
                    ('write_date', '>=', five_days_ago)
                ], order='write_date desc', limit=3)
                for rl in refused_leaves:
                    st_name = rl.holiday_status_id.name if rl.holiday_status_id else 'إجازة'
                    reason = getattr(rl, 'report_note', False) or getattr(rl, 'notes', '') or ''
                    reason_str = f" - السبب: {reason}" if reason else ""
                    alerts.append({
                        'id': f'leave_ref_{rl.id}',
                        'type': 'leave_refused',
                        'title': 'تم رفض طلب إجازتك ❌',
                        'body': f'عذراً، لم تتم الموافقة على طلب ({st_name}){reason_str}',
                        'url': '/my/leaves',
                        'tag': f'leave_ref_{rl.id}'
                    })
            except Exception as e_lv:
                _logger.warning("Error polling leaves: %s", e_lv)

            # 5. Location Requests (طلبات العمل الميداني / المواقع المؤقتة)
            try:
                # 5.a Employee recent location approvals/refusals
                loc_reqs = request.env['jandalah.location.request'].sudo().search([
                    ('employee_id', '=', emp.id),
                    ('state', 'in', ['approve', 'refuse']),
                    ('write_date', '>=', five_days_ago)
                ], order='write_date desc', limit=3)
                for lr in loc_reqs:
                    if lr.state == 'approve':
                        alerts.append({
                            'id': f'loc_appr_{lr.id}',
                            'type': 'location_approved',
                            'title': 'تم اعتماد موقعك الميداني ✅',
                            'body': f'تمت الموافقة على تواجدك في ({lr.temp_location_name}) من تاريخ {lr.date_from}.',
                            'url': '/my',
                            'tag': f'loc_appr_{lr.id}'
                        })
                    elif lr.state == 'refuse':
                        r_str = f" - السبب: {lr.refusal_reason}" if lr.refusal_reason else ""
                        alerts.append({
                            'id': f'loc_ref_{lr.id}',
                            'type': 'location_refused',
                            'title': 'تم رفض طلب الموقع الميداني ❌',
                            'body': f'لم تتم الموافقة على موقع ({lr.temp_location_name}){r_str}',
                            'url': '/my',
                            'tag': f'loc_ref_{lr.id}'
                        })
            except Exception as e_loc:
                _logger.warning("Error polling location requests: %s", e_loc)

            # 6. Supervisor Pending Team Requests (إجازات ومواقع الفريق)
            try:
                subordinates = self._get_subordinates(emp)
                if subordinates:
                    # Team leaves
                    team_leaves = request.env['hr.leave'].sudo().search([
                        ('employee_id', 'in', subordinates.ids),
                        ('state', 'in', ['confirm', 'validate1'])
                    ], order='create_date desc', limit=5)
                    for tl in team_leaves:
                        st_name = tl.holiday_status_id.name if tl.holiday_status_id else 'إجازة'
                        alerts.append({
                            'id': f'team_leave_{tl.id}',
                            'type': 'team_leave',
                            'title': 'طلب إجازة جديد لفريقك 📋',
                            'body': f'الموظف {tl.employee_id.name} تقدم بطلب ({st_name}) بانتظار موافقتك.',
                            'url': '/my/team/leaves',
                            'tag': f'team_leave_{tl.id}'
                        })
                    # Team location requests
                    team_locs = request.env['jandalah.location.request'].sudo().search([
                        ('employee_id', 'in', subordinates.ids),
                        ('state', '=', 'confirm')
                    ], order='create_date desc', limit=5)
                    for tloc in team_locs:
                        alerts.append({
                            'id': f'team_loc_{tloc.id}',
                            'type': 'team_location',
                            'title': 'طلب موقع ميداني جديد 📍',
                            'body': f'الموظف {tloc.employee_id.name} يطلب اعتماد العمل من ({tloc.temp_location_name})',
                            'url': '/my',
                            'tag': f'team_loc_{tloc.id}'
                        })
            except Exception as e_tlv:
                _logger.warning("Error polling team requests: %s", e_tlv)

            # 7. Shift Completion & Morning Punch Reminders
            try:
                user_tz_name = get_current_user_tz()
                user_tz = pytz.timezone(user_tz_name)
                now_local = pytz.utc.localize(datetime.utcnow()).astimezone(user_tz)
                today_local_date = now_local.date()

                open_att = request.env['hr.attendance'].sudo().search([
                    ('employee_id', '=', emp.id),
                    ('check_out', '=', False)
                ], order='check_in desc', limit=1)

                is_punched_today = False
                if open_att and open_att.check_in:
                    check_in_local = pytz.utc.localize(open_att.check_in).astimezone(user_tz)
                    if check_in_local.date() == today_local_date:
                        is_punched_today = True
                        # Overdue Shift End Reminder (>= 8.25 hours)
                        if (now_local - check_in_local).total_seconds() / 3600.0 >= 8.25:
                            alerts.append({
                                'id': f'att_overdue_{open_att.id}_{now_local.strftime("%Y%m%d")}',
                                'type': 'overdue_attendance',
                                'title': 'تنبيه انتهاء الدوام ⏰',
                                'body': f'مرحباً {emp.name}، لقد أتممت ساعات العمل المقررة اليوم. يرجى تسجيل الانصراف الآن.',
                                'url': '/my',
                                'tag': f'att_overdue_{open_att.id}'
                            })

                # Morning Punch Reminder (between 7:30 AM and 12:30 PM if not checked in today)
                if not is_punched_today and 7 <= now_local.hour < 13:
                    # check if any attendance was closed today
                    done_att_today = request.env['hr.attendance'].sudo().search_count([
                        ('employee_id', '=', emp.id),
                        ('check_in', '>=', now_local.replace(hour=0, minute=0, second=0).astimezone(pytz.utc).strftime('%Y-%m-%d %H:%M:%S'))
                    ])
                    if done_att_today == 0:
                        alerts.append({
                            'id': f'att_morning_{emp.id}_{now_local.strftime("%Y%m%d")}',
                            'type': 'morning_attendance',
                            'title': 'صباح الخير | تذكير تسجيل الحضور 🌅',
                            'body': f'صباح الخير {emp.name}، تذكير بتسجيل بصمة الحضور بالـ GPS لبدء دوامك اليومي.',
                            'url': '/my',
                            'tag': f'att_morning_{now_local.strftime("%Y%m%d")}'
                        })
            except Exception as e_att:
                _logger.warning("Error polling attendance: %s", e_att)

        return {
            'status': 'success',
            'alerts': alerts,
            'notifications': alerts,
            'total_active': len(alerts)
        }

    @http.route('/my/notifications/test_simulate', type='json', auth='user', methods=['POST'], csrf=False)
    def notifications_test_simulate(self, sim_type=None, **kw):
        """Allows Admin/HR manager to test-fire any notification instantly on their phone."""
        user = request.env.user
        emp = self._get_employee()
        emp_name = emp.name if emp else user.name

        samples = {
            'announcement': {
                'title': 'تعميم إداري جديد 📢',
                'body': 'صادر عن الإدارة العامة: مواعيد العمل الرسمية والتعليمات المحدثة.',
                'url': '/my/announcements'
            },
            'leave_approved': {
                'title': 'تمت الموافقة على طلب إجازتك ✅',
                'body': f'تهانينا {emp_name}، تم اعتماد طلب إجازتك السنوية بنجاح من قبل الإدارة.',
                'url': '/my/leaves'
            },
            'leave_refused': {
                'title': 'تم رفض طلب إجازتك ❌',
                'body': f'عذراً {emp_name}، تعذر اعتماد طلب الإجازة لتعارضها مع جدول تغطية الفريق.',
                'url': '/my/leaves'
            },
            'asset': {
                'title': 'إقرار استلام عهدة جديدة 💻',
                'body': 'تم تسليمك عهدة جديدة (حاسوب محمول Dell Latitude). يرجى تأكيد استلامها.',
                'url': '/my/assets'
            },
            'penalty': {
                'title': 'تنبيه إداري رسمي ⚠️',
                'body': 'صدر قرار لفت نظر إداري برقم (PEN-042). يرجى الاطلاع والرد.',
                'url': '/my/penalties'
            },
            'location': {
                'title': 'طلب موقع ميداني جديد 📍',
                'body': f'الموظف {emp_name} يطلب اعتماد العمل من موقع عميل خارجي بالـ GPS.',
                'url': '/my'
            },
            'morning': {
                'title': 'صباح الخير | تذكير تسجيل الحضور 🌅',
                'body': f'صباح الخير {emp_name}، تذكير بتسجيل بصمة الحضور بالـ GPS لبدء دوامك اليومي.',
                'url': '/my'
            },
            'overdue': {
                'title': 'تنبيه انتهاء ساعات الدوام ⏰',
                'body': f'مرحباً {emp_name}، لقد أتممت 8 ساعات عمل اليوم. يرجى تسجيل بصمة الانصراف.',
                'url': '/my'
            }
        }
        sim = samples.get(sim_type, samples['announcement']).copy()
        sim['id'] = f"sim_{sim_type}_{int(datetime.utcnow().timestamp())}"
        sim['tag'] = f"jandalah-sim-{sim_type}-{int(datetime.utcnow().timestamp())}"
        return {'status': 'success', 'notification': sim}

    @http.route('/my/attendance/toggle', type='json', auth='user', methods=['POST'], csrf=False)
    def attendance_toggle(self, latitude=None, longitude=None, browser=None, tz=None, **kw):
        # Handles 1-tap Check-In and Check-Out with GPS, IP, and Browser capture
        emp = self._get_employee()
        if not emp:
            return {
                'status': 'error',
                'message': 'لم يتم العثور على ملف موظف مرتبط بحسابك.'
            }

        # IP Address capture
        ip_addr = request.httprequest.headers.get('X-Forwarded-For', request.httprequest.remote_addr)
        if ip_addr and ',' in ip_addr:
            ip_addr = ip_addr.split(',')[0].strip()

        now_utc = fields.Datetime.now()
        formatted_time = format_tz_dynamic(now_utc, tz_name=tz)

        # STRICT GPS REQUIREMENT: Geolocation is mandatory for attendance
        if not latitude or not longitude:
            return {
                'status': 'error',
                'message': 'تحديد الموقع الجغرافي (GPS) إلزامي لتسجيل البصمة. يرجى تفعيل خدمة الموقع ومنح الصلاحية للمتصفح.'
            }

        user_tz_name = get_current_user_tz(tz)
        user_tz = pytz.timezone(user_tz_name)
        now_local = pytz.utc.localize(datetime.utcnow()).astimezone(user_tz)
        today_local_date = now_local.date()

        # Check for open attendance
        open_att = request.env['hr.attendance'].sudo().search([
            ('employee_id', '=', emp.id),
            ('check_out', '=', False)
        ], order='check_in desc', limit=1)

        is_valid_open_today = False
        if open_att and open_att.check_in:
            check_in_local = pytz.utc.localize(open_att.check_in).astimezone(user_tz)
            if check_in_local.date() == today_local_date:
                is_valid_open_today = True

        if is_valid_open_today:
            # CHECK OUT LOCALLY
            out_vals = {
                'check_out': now_utc,
                'out_ip_address': ip_addr or 'N/A',
                'out_browser': browser or 'Mobile/Web Portal',
            }
            if latitude and longitude:
                try:
                    out_vals['out_latitude'] = float(latitude)
                    out_vals['out_longitude'] = float(longitude)
                except (ValueError, TypeError):
                    pass

            open_att.sudo().write(out_vals)
            worked_hours = round(open_att.worked_hours, 2) if open_att.worked_hours else 0.0

            # SYNC CHECK-OUT TO ENTERPRISE VIA API
            sync_attendance_to_enterprise(
                employee_name=emp.name,
                employee_email=emp.work_email,
                employee_barcode=emp.barcode,
                action='checked_out',
                check_time=now_utc,
                lat=latitude,
                lon=longitude,
                ip=ip_addr,
                browser=browser
            )

            return {
                'status': 'success',
                'action': 'checked_out',
                'message': f'تم تسجيل الانصراف بنجاح الساعة {formatted_time}',
                'time': formatted_time,
                'worked_hours': worked_hours,
                'employee_name': emp.name,
            }
        else:
            # CHECK IN LOCALLY WITH GEOFENCING
            in_vals = {
                'employee_id': emp.id,
                'check_in': now_utc,
                'in_ip_address': ip_addr or 'N/A',
                'in_browser': browser or 'Mobile/Web Portal',
            }

            distance_m = 0.0
            is_outside = False
            effective_loc_name = 'المقر المعتمد'

            if latitude and longitude:
                try:
                    lat_f = float(latitude)
                    lon_f = float(longitude)
                    in_vals['in_latitude'] = lat_f
                    in_vals['in_longitude'] = lon_f

                    # Check geofence against effective location (including Location 1, Location 2, and approved temporary locations)
                    if hasattr(emp.sudo(), 'get_effective_work_location'):
                        eff_loc = emp.sudo().get_effective_work_location()
                        effective_loc_name = eff_loc.get('location_name', 'المقر المعتمد')
                        work_lat = eff_loc.get('latitude', 0.0)
                        work_lon = eff_loc.get('longitude', 0.0)
                        work_lat_2 = eff_loc.get('latitude_2')
                        work_lon_2 = eff_loc.get('longitude_2')
                        radius = eff_loc.get('radius', 1000) or 1000

                        distances = []
                        if work_lat and work_lon and hasattr(emp, 'calculate_distance_meters'):
                            d1 = emp.calculate_distance_meters(lat_f, lon_f, work_lat, work_lon)
                            distances.append(d1)
                        if work_lat_2 and work_lon_2 and hasattr(emp, 'calculate_distance_meters'):
                            d2 = emp.calculate_distance_meters(lat_f, lon_f, work_lat_2, work_lon_2)
                            distances.append(d2)

                        if distances:
                            min_dist = min(distances)
                            distance_m = min_dist
                            in_vals['distance_from_work'] = round(distance_m, 1)
                            if radius > 0 and min_dist > radius:
                                is_outside = True
                                in_vals['is_outside_geofence'] = True
                            else:
                                is_outside = False
                                in_vals['is_outside_geofence'] = False
                except (ValueError, TypeError):
                    pass

            new_att = request.env['hr.attendance'].sudo().create(in_vals)

            # SYNC CHECK-IN TO ENTERPRISE VIA API
            sync_attendance_to_enterprise(
                employee_name=emp.name,
                employee_email=emp.work_email,
                employee_barcode=emp.barcode,
                action='checked_in',
                check_time=now_utc,
                lat=latitude,
                lon=longitude,
                ip=ip_addr,
                browser=browser
            )

            return {
                'status': 'success',
                'action': 'checked_in',
                'message': f'تم تسجيل الحضور بنجاح الساعة {formatted_time}',
                'time': formatted_time,
                'employee_name': emp.name,
            }

    @http.route(['/my/attendance', '/my/attendance/page/<int:page>'], type='http', auth='user', website=True)
    def portal_my_attendance(self, page=1, **kw):
        emp = self._get_employee()
        if not emp:
            return request.render('portal.portal_my_home', {
                'error_message': 'الموظف غير موجود أو غير مرتبط بحسابك'
            })

        Attendance = request.env['hr.attendance'].sudo()
        domain = [('employee_id', '=', emp.id)]
        att_count = Attendance.search_count(domain)

        items_per_page = 20
        pager = portal_pager(
            url='/my/attendance',
            total=att_count,
            page=page,
            step=items_per_page
        )

        offset = (page - 1) * items_per_page
        attendances = Attendance.search(domain, order='check_in desc', limit=items_per_page, offset=offset)

        user_tz_name = get_current_user_tz()
        user_tz = pytz.timezone(user_tz_name)
        now_local = pytz.utc.localize(datetime.utcnow()).astimezone(user_tz)
        today_local_date = now_local.date()

        open_att = Attendance.search([
            ('employee_id', '=', emp.id),
            ('check_out', '=', False)
        ], order='check_in desc', limit=1)

        is_checked_in = False
        if open_att and open_att.check_in:
            check_in_local = pytz.utc.localize(open_att.check_in).astimezone(user_tz)
            if check_in_local.date() == today_local_date:
                is_checked_in = True

        all_emp_att = Attendance.search(domain)
        total_hours = round(sum(a.worked_hours for a in all_emp_att if a.worked_hours), 2)

        # Process attendances with midnight rule, location approval, and morning delay (19-min grace)
        attendances_data = []
        for a in attendances:
            in_local = pytz.utc.localize(a.check_in).astimezone(user_tz) if a.check_in else None
            out_local = pytz.utc.localize(a.check_out).astimezone(user_tz) if a.check_out else None

            # Midnight rule for checkout: If unpunched and date is prior to today, show '-' (unpunched)
            is_unpunched_checkout = False
            out_display = '-'
            if out_local:
                out_display = format_tz_dynamic(a.check_out, tz_name=user_tz_name, time_only=True)
            elif in_local and in_local.date() == today_local_date:
                out_display = 'مفتوح حالياً'
            else:
                out_display = '-'
                is_unpunched_checkout = True

            # Location approval status
            location_status = 'none'
            if a.in_latitude and a.in_longitude:
                location_status = 'rejected' if a.is_outside_geofence else 'approved'

            # Morning delay calculation: dynamic work start from calendar/contract, 19 min grace period
            delay_minutes = 0
            is_late = False
            is_leave_day = getattr(a, 'is_leave_day', False)
            if in_local and not is_leave_day:
                try:
                    work_start_min = 8 * 60  # Default 8:00 AM
                    calendar = None
                    emp_sudo = emp.sudo() if emp else False
                    contract = getattr(emp_sudo, 'contract_id', None) if emp_sudo else None
                    if contract and contract.sudo().resource_calendar_id:
                        calendar = contract.sudo().resource_calendar_id
                    elif hasattr(emp_sudo, 'contract_ids'):
                        running = emp_sudo.contract_ids.filtered(lambda c: c.state == 'open' and c.resource_calendar_id)
                        if running:
                            calendar = running[0].sudo().resource_calendar_id
                        else:
                            with_cal = emp_sudo.contract_ids.filtered(lambda c: c.resource_calendar_id)
                            if with_cal:
                                calendar = with_cal[0].sudo().resource_calendar_id

                    if not calendar and emp_sudo and emp_sudo.resource_calendar_id:
                        calendar = emp_sudo.resource_calendar_id
                    elif not calendar and emp_sudo and emp_sudo.company_id and emp_sudo.company_id.sudo().resource_calendar_id:
                        calendar = emp_sudo.company_id.sudo().resource_calendar_id

                    if calendar and calendar.sudo().attendance_ids:
                        w_day = str(in_local.weekday())
                        sched = calendar.sudo().attendance_ids.filtered(lambda s: s.dayofweek == w_day)
                        if not sched:
                            sched = calendar.sudo().attendance_ids
                        if sched:
                            work_start_min = int(round(min(sched.mapped('hour_from')) * 60))

                    punch_min = in_local.hour * 60 + in_local.minute
                    grace_min = 19
                    if punch_min > (work_start_min + grace_min):
                        delay_minutes = punch_min - work_start_min
                        is_late = True
                except Exception as e_att_cal:
                    _logger.warning("Error computing attendance calendar delay: %s", e_att_cal)

            # Check for approved permission on this attendance date to deduct hours from delay
            att_date = in_local.date() if in_local else None
            is_perm = False
            perm_time_str = ''
            if att_date and emp:
                appr_l = request.env['hr.leave'].sudo().search([
                    ('employee_id', '=', emp.id),
                    ('state', '=', 'validate'),
                    ('request_date_from', '<=', att_date),
                    ('request_date_to', '>=', att_date),
                ], limit=1)
                if appr_l:
                    t_name = appr_l.holiday_status_id.name or ''
                    if (getattr(appr_l, 'request_unit_hours', False) or
                        (hasattr(appr_l, 'number_of_hours_display') and appr_l.number_of_hours_display > 0 and appr_l.number_of_hours_display < 8) or
                        any(k in t_name for k in ['إذن', 'اذن', 'استئذان', 'ساعي', 'خروج'])):
                        is_perm = True
                        h_val = getattr(appr_l, 'number_of_hours_display', 0.0)
                        if not h_val and getattr(appr_l, 'number_of_days', 0.0):
                            h_val = appr_l.number_of_days * 8.0
                        h_val = min(h_val, 3.0) if h_val else 3.0
                        perm_min = int(round(h_val * 60))
                        perm_time_str = f"{int(h_val)} س" if h_val.is_integer() else f"{h_val:g} س"
                        if delay_minutes > 0 and perm_min > 0:
                            delay_minutes = max(0, delay_minutes - perm_min)
                            if delay_minutes == 0:
                                is_late = False

            delay_display = 'على الموعد'
            if is_late and delay_minutes > 0:
                dh = delay_minutes // 60
                dm = delay_minutes % 60
                d_time_str = f"{dh}:{dm:02d} س"
                if is_perm:
                    delay_display = f"متأخر ({d_time_str} بعد الإذن)"
                else:
                    delay_display = f"متأخر ({d_time_str})"
            elif is_perm:
                delay_display = f"إذن معتمد ({perm_time_str})"
            elif is_leave_day or not in_local:
                delay_display = '-'

            # Formatted check_in and check_out
            check_in_str = '-' if is_leave_day else (format_tz_dynamic(a.check_in, tz_name=user_tz_name, time_only=True) if a.check_in else '-')
            check_out_str = '-' if is_leave_day else out_display

            attendances_data.append({
                'id': a.id,
                'record': a,
                'date': in_local.strftime('%Y/%m/%d') if in_local else '-',
                'check_in': check_in_str,
                'check_out': check_out_str,
                'worked_hours': ('-' if is_leave_day else (f"{a.worked_hours:.2f} س" if a.worked_hours and not is_unpunched_checkout else ('-' if is_unpunched_checkout else 'قيد العمل'))),
                'is_unpunched_checkout': is_unpunched_checkout and not is_leave_day,
                'is_open': not a.check_out and not is_unpunched_checkout and not is_leave_day,
                'is_leave_day': is_leave_day,
                'leave_display': getattr(a, 'leave_permission_display', '') or '',
                'location_status': 'none' if is_leave_day else location_status,
                'in_lat': False if is_leave_day else a.in_latitude,
                'in_lon': False if is_leave_day else a.in_longitude,
                'out_lat': False if is_leave_day else a.out_latitude,
                'out_lon': False if is_leave_day else a.out_longitude,
                'delay_minutes': delay_minutes,
                'delay_display': delay_display,
                'is_late': is_late,
            })

        values = self._prepare_portal_layout_values()
        values.update({
            'employee': emp,
            'attendances': attendances,
            'attendances_data': attendances_data,
            'open_attendance': open_att if is_checked_in else False,
            'is_checked_in': is_checked_in,
            'total_hours': total_hours,
            'page_name': 'attendance',
            'pager': pager,
            'format_tz': format_tz_dynamic,
            'default_url': '/my/attendance',
        })
        return request.render('jandalah_employee_portal.portal_my_attendance_view', values)

    @http.route(['/my/leaves', '/my/leaves/page/<int:page>'], type='http', auth='user', website=True)
    def portal_my_leaves(self, page=1, success=None, error=None, **kw):
        emp = self._get_employee()
        if not emp:
            return request.render('portal.portal_my_home', {
                'error_message': 'لا يوجد ملف موظف مرتبط'
            })

        Leave = request.env['hr.leave'].sudo()
        domain = [('employee_id', '=', emp.id)]
        leaves_count = Leave.search_count(domain)

        items_per_page = 20
        pager = portal_pager(
            url='/my/leaves',
            total=leaves_count,
            page=page,
            step=items_per_page
        )

        offset = (page - 1) * items_per_page
        raw_leaves = Leave.search(domain, order='request_date_from desc', limit=items_per_page, offset=offset)
        leave_types = request.env['hr.leave.type'].sudo().search([('active', '=', True)])
        leave_types_data = []
        for lt in leave_types:
            is_hourly = False
            if hasattr(lt, 'request_unit') and lt.request_unit == 'hour':
                is_hourly = True
            elif any(k in (lt.name or '') for k in ['إذن', 'اذن', 'استئذان', 'ساعي', 'ساعات', 'خروج']):
                is_hourly = True
            leave_types_data.append({
                'id': lt.id,
                'name': lt.name,
                'is_hourly': is_hourly,
            })

        # Pre-extract all leave data into pure safe Python dicts to eliminate QWeb AttributeError / AccessError
        leaves_data = []
        for l in raw_leaves:
            leave_type = l.holiday_status_id.name if l.holiday_status_id else 'إجازة'
            has_attachment = False
            try:
                if 'supported_attachment_ids' in l._fields and l.supported_attachment_ids:
                    has_attachment = True
                elif 'message_main_attachment_id' in l._fields and l.message_main_attachment_id:
                    has_attachment = True
                elif request.env['ir.attachment'].sudo().search_count([('res_model', '=', 'hr.leave'), ('res_id', '=', l.id)]) > 0:
                    has_attachment = True
            except Exception:
                pass

            refusal_reason = ''
            if l.state == 'refuse':
                if 'report_note' in l._fields and l.report_note:
                    refusal_reason = l.report_note
                elif 'notes' in l._fields and l.notes:
                    refusal_reason = l.notes

            date_from_val = l.request_date_from or (l.date_from.date() if l.date_from else '')
            date_to_val = l.request_date_to or (l.date_to.date() if l.date_to else '')

            is_hourly_req = (
                getattr(l, 'request_unit_hours', False) or
                (hasattr(l, 'number_of_hours_display') and l.number_of_hours_display > 0 and l.number_of_hours_display < 8) or
                any(k in leave_type for k in ['إذن', 'اذن', 'استئذان', 'ساعي', 'خروج'])
            )
            hours_disp = ''
            if is_hourly_req:
                h_val = getattr(l, 'number_of_hours_display', 0.0) or (l.number_of_days * 8.0 if l.number_of_days < 1 else 3.0)
                h_val = min(h_val, 3.0)
                hours_disp = f"{int(h_val)} س" if h_val.is_integer() else f"{h_val:g} س"

            state_labels = {
                'confirm': 'بانتظار موافقة المدير المباشر',
                'validate1': 'بانتظار موافقة الموارد البشرية',
                'validate': 'معتمدة',
                'refuse': 'مرفوضة',
                'draft': 'مسودة',
                'cancel': 'ملغاة',
            }
            leaves_data.append({
                'id': l.id,
                'holiday_type': leave_type,
                'request_date_from': str(date_from_val or '-'),
                'request_date_to': str(date_to_val or '-'),
                'number_of_days': l.number_of_days or 1.0,
                'is_hourly': is_hourly_req,
                'hours_display': hours_disp,
                'name': l.name or '',
                'state': l.state,
                'state_desc': state_labels.get(l.state, l.state),
                'refusal_reason': refusal_reason,
                'has_attachment': has_attachment,
            })

        values = self._prepare_portal_layout_values()
        # Compute leave balances and permission stats for the leaves page
        leave_info = self._get_employee_leave_balances(emp)
        leave_balances_page = leave_info.get('balances', [])
        monthly_perm_used = leave_info.get('monthly_perm_used', 0.0)
        monthly_perm_remaining = leave_info.get('monthly_perm_remaining', 9.0)
        monthly_perm_text = leave_info.get('monthly_perm_text', '9 ساعات')
        monthly_perm_month_name = leave_info.get('monthly_perm_month_name', '')

        values.update({
            'employee': emp,
            'leaves': leaves_data,
            'leave_types': leave_types_data,
            'leave_balances': leave_balances_page,
            'is_in_probation': leave_info.get('is_in_probation', False),
            'probation_end_date': leave_info.get('probation_end_date', ''),
            'monthly_perm_used': monthly_perm_used,
            'monthly_perm_remaining': monthly_perm_remaining,
            'monthly_perm_text': monthly_perm_text,
            'monthly_perm_month_name': monthly_perm_month_name,
            'page_name': 'leaves',
            'pager': pager,
            'success': success,
            'error': error,
            'format_tz': format_tz_dynamic,
            'default_url': '/my/leaves',
        })
        return request.render('jandalah_employee_portal.portal_my_leaves_view', values)

    @http.route('/my/leaves/submit', type='http', auth='user', methods=['POST'], website=True, csrf=True)
    def portal_leave_submit(self, holiday_status_id=None, request_date_from=None, request_date_to=None, name=None, permission_date=None, request_hour_from=None, request_hour_to=None, permission_hours=None, is_hourly=None, **kw):
        emp = self._get_employee()
        if not emp:
            return request.redirect('/my/leaves?error=no_employee')

        if not holiday_status_id:
            return request.redirect('/my/leaves?error=missing_fields')

        lt = request.env['hr.leave.type'].sudo().browse(int(holiday_status_id))
        is_hourly_type = (
            is_hourly == '1' or
            bool(permission_date) or
            (hasattr(lt, 'request_unit') and lt.request_unit == 'hour') or
            any(k in (lt.name or '') for k in ['إذن', 'اذن', 'استئذان', 'ساعي', 'ساعات', 'خروج'])
        )

        try:
            calc_hours = 2.0
            if is_hourly_type:
                if hasattr(lt, 'request_unit') and lt.request_unit != 'hour':
                    try:
                        lt.sudo().write({'request_unit': 'hour'})
                    except Exception as e_lt:
                        _logger.warning("Could not set lt request_unit to hour: %s", e_lt)

                perm_date = permission_date or request_date_from or str(fields.Date.today())
                h_from = request_hour_from or '09:00'
                h_to = request_hour_to or '12:00'

                try:
                    if permission_hours:
                        calc_hours = float(permission_hours)
                    elif h_from and h_to:
                        hf = [int(x) for x in h_from.split(':')]
                        ht = [int(x) for x in h_to.split(':')]
                        calc_hours = (ht[0] + ht[1]/60.0) - (hf[0] + hf[1]/60.0)
                        if calc_hours <= 0:
                            calc_hours = 2.0
                except Exception:
                    calc_hours = 2.0

                calc_hours = round(calc_hours, 2)

                # 1. Daily Limit Check: Strictly Maximum 3 hours per day
                if calc_hours > 3.0:
                    err_msg = f"لا يمكن تنفيذ الطلب: الحد الأقصى للإذن هو 3 ساعات في اليوم الواحد (طلبت {calc_hours:g} س)."
                    return request.redirect(f'/my/leaves?error={urllib.parse.quote(err_msg)}')

                LeaveReq = request.env['hr.leave'].sudo()
                same_day_leaves = LeaveReq.search([
                    ('employee_id', '=', emp.id),
                    ('state', 'in', ['confirm', 'validate1', 'validate']),
                    ('request_date_from', '=', perm_date),
                ])
                existing_day_h = 0.0
                for sdl in same_day_leaves:
                    s_tname = sdl.holiday_status_id.name or ''
                    if (getattr(sdl, 'request_unit_hours', False) or
                        (hasattr(sdl, 'number_of_hours_display') and sdl.number_of_hours_display > 0 and sdl.number_of_hours_display < 8) or
                        any(k in s_tname for k in ['إذن', 'اذن', 'استئذان', 'ساعي', 'خروج'])):
                        existing_day_h += getattr(sdl, 'number_of_hours_display', 0.0) or (sdl.number_of_days * 8.0 if sdl.number_of_days < 1 else 3.0)

                if (existing_day_h + calc_hours) > 3.0:
                    rem_day = max(0.0, 3.0 - existing_day_h)
                    err_msg = f"لا يمكن تنفيذ الطلب: تجاوزت الحد اليومي (3 ساعات). لديك بالفعل {existing_day_h:g} س مسجلة لهذا اليوم والمتبقي لك {rem_day:g} س فقط."
                    return request.redirect(f'/my/leaves?error={urllib.parse.quote(err_msg)}')

                # 2. Monthly Limit Check: Strictly Maximum 9 hours per month (renews monthly)
                try:
                    p_date_obj = fields.Date.from_string(perm_date)
                except Exception:
                    p_date_obj = date.today()
                m_start = p_date_obj.replace(day=1)
                if p_date_obj.month == 12:
                    m_end = p_date_obj.replace(year=p_date_obj.year + 1, month=1, day=1) - timedelta(days=1)
                else:
                    m_end = p_date_obj.replace(month=p_date_obj.month + 1, day=1) - timedelta(days=1)

                month_leaves = LeaveReq.search([
                    ('employee_id', '=', emp.id),
                    ('state', 'in', ['confirm', 'validate1', 'validate']),
                    ('request_date_from', '>=', str(m_start)),
                    ('request_date_from', '<=', str(m_end)),
                ])
                monthly_used_h = 0.0
                for ml in month_leaves:
                    m_tname = ml.holiday_status_id.name or ''
                    if (getattr(ml, 'request_unit_hours', False) or
                        (hasattr(ml, 'number_of_hours_display') and ml.number_of_hours_display > 0 and ml.number_of_hours_display < 8) or
                        any(k in m_tname for k in ['إذن', 'اذن', 'استئذان', 'ساعي', 'خروج'])):
                        monthly_used_h += getattr(ml, 'number_of_hours_display', 0.0) or (ml.number_of_days * 8.0 if ml.number_of_days < 1 else 3.0)

                # 3. Monthly Permissions Count Check: Maximum 3 requests per month
                perm_requests_count = sum(
                    1 for ml in month_leaves
                    if (getattr(ml, 'request_unit_hours', False) or
                        (hasattr(ml, 'number_of_hours_display') and ml.number_of_hours_display > 0 and ml.number_of_hours_display < 8) or
                        any(k in (ml.holiday_status_id.name or '') for k in ['إذن', 'اذن', 'استئذان', 'ساعي', 'خروج']))
                )
                if perm_requests_count >= 3:
                    err_msg = "لا يمكن تنفيذ الطلب: تجاوزت الحد الأقصى لعدد الأذونات المسموحة شهرياً (3 أذونات فقط في الشهر الميلادي الواحد)."
                    return request.redirect(f'/my/leaves?error={urllib.parse.quote(err_msg)}')

                if (monthly_used_h + calc_hours) > 9.0:
                    rem_month = max(0.0, 9.0 - monthly_used_h)
                    err_msg = f"لا يمكن تنفيذ الطلب: تجاوزت الحد الأقصى الشهري للأذونات (9 ساعات شهرياً). استهلكت هذا الشهر {monthly_used_h:g} س والمتبقي لك هو {rem_month:g} س فقط (طلبت {calc_hours:g} س)."
                    return request.redirect(f'/my/leaves?error={urllib.parse.quote(err_msg)}')

                hf = [int(x) for x in h_from.split(':')]
                ht = [int(x) for x in h_to.split(':')]
                h_from_float = round(hf[0] + hf[1] / 60.0, 2)
                h_to_float = round(ht[0] + ht[1] / 60.0, 2)

                leave_vals = {
                    'holiday_status_id': lt.id,
                    'employee_id': emp.id,
                    'request_date_from': perm_date,
                    'request_date_to': perm_date,
                    'name': name.strip() if name else f"طلب إذن استئذان ({calc_hours:g} س)",
                    'number_of_days': round(calc_hours / 8.0, 4),
                }
                if 'request_unit_hours' in request.env['hr.leave']._fields:
                    leave_vals['request_unit_hours'] = True
                if 'request_hour_from' in request.env['hr.leave']._fields:
                    f_from = request.env['hr.leave']._fields['request_hour_from']
                    leave_vals['request_hour_from'] = h_from_float if f_from.type == 'float' else f"{h_from_float:g}"
                if 'request_hour_to' in request.env['hr.leave']._fields:
                    f_to = request.env['hr.leave']._fields['request_hour_to']
                    leave_vals['request_hour_to'] = h_to_float if f_to.type == 'float' else f"{h_to_float:g}"
                if 'number_of_hours_display' in request.env['hr.leave']._fields:
                    leave_vals['number_of_hours_display'] = calc_hours

                user_tz_name = get_current_user_tz()
                user_tz = pytz.timezone(user_tz_name)
                try:
                    d_obj = fields.Date.from_string(perm_date)
                    dt_f = user_tz.localize(datetime.combine(d_obj, datetime.time(hf[0], hf[1])))
                    dt_t = user_tz.localize(datetime.combine(d_obj, datetime.time(ht[0], ht[1])))
                    leave_vals['date_from'] = dt_f.astimezone(pytz.utc).replace(tzinfo=None)
                    leave_vals['date_to'] = dt_t.astimezone(pytz.utc).replace(tzinfo=None)
                except Exception as e_time:
                    _logger.warning("Error computing permission datetime: %s", e_time)

            else:
                # 6-Month probation rule check before allowing leave submission
                start_date = getattr(emp, 'first_contract_date', False) or (emp.contract_id.date_start if getattr(emp, 'contract_id', False) else False)
                if start_date:
                    from dateutil.relativedelta import relativedelta
                    six_months_date = start_date + relativedelta(months=6)
                    if date.today() < six_months_date:
                        err_msg = f"عذراً، لا يمكن تقديم طلب إجازة قبل إتمام 6 أشهر كاملة من بداية العمل أو العقد (تاريخ استحقاق الإجازات: {six_months_date.strftime('%Y-%m-%d')})."
                        return request.redirect(f'/my/leaves?error={urllib.parse.quote(err_msg)}')

                # Support Half-Day Leave (0.5 day deducted from regular balance)
                is_half_day = (kw.get('is_half_day') == '1' or kw.get('request_unit_half') == '1')
                if is_half_day:
                    if not request_date_from:
                        return request.redirect('/my/leaves?error=missing_fields')
                    request_date_to = request_date_from
                    requested_days = 0.5
                else:
                    if not request_date_from or not request_date_to:
                        return request.redirect('/my/leaves?error=missing_fields')

                    d_from = fields.Date.from_string(request_date_from)
                    d_to = fields.Date.from_string(request_date_to)
                    if not d_from or not d_to or d_to < d_from:
                        err_msg = "تاريخ نهاية الإجازة يجب أن يكون مساوياً أو بعد تاريخ البداية."
                        return request.redirect(f'/my/leaves?error={urllib.parse.quote(err_msg)}')

                    requested_days = (d_to - d_from).days + 1

                # 1. Casual leave limit: Max 2 consecutive days
                if 'عارضة' in (lt.name or '') and requested_days > 2:
                    err_msg = "لا يمكن تقديم الطلب: الحد الأقصى للإجازة العارضة هو يومان متتاليان فقط في المرة الواحدة."
                    return request.redirect(f'/my/leaves?error={urllib.parse.quote(err_msg)}')

                # 2. Strict Balance limit check: Prevent exceeding available balance
                LeaveAlloc = request.env['hr.leave.allocation'].sudo()
                LeaveReq = request.env['hr.leave'].sudo()

                alloc_records = LeaveAlloc.search([
                    ('employee_id', '=', emp.id),
                    ('holiday_status_id', '=', lt.id),
                    ('state', '=', 'validate')
                ])
                total_allocated = sum(alloc_records.mapped('number_of_days'))

                taken_records = LeaveReq.search([
                    ('employee_id', '=', emp.id),
                    ('holiday_status_id', '=', lt.id),
                    ('state', 'in', ['validate', 'validate1', 'confirm'])
                ])
                total_taken = sum(taken_records.mapped('number_of_days'))

                requires_alloc = getattr(lt, 'requires_allocation', 'yes') != 'no'
                if requires_alloc or total_allocated > 0:
                    remaining_balance = max(0.0, total_allocated - total_taken)
                    if requested_days > remaining_balance:
                        rem_str = int(remaining_balance) if remaining_balance.is_integer() else f"{remaining_balance:g}"
                        err_msg = f"لا يمكن تقديم الطلب: رصيدك المتبقي المتاح من ({lt.name}) هو {rem_str} يوم فقط، بينما طلبت {requested_days} يوم."
                        return request.redirect(f'/my/leaves?error={urllib.parse.quote(err_msg)}')

                leave_vals = {
                    'holiday_status_id': lt.id,
                    'employee_id': emp.id,
                    'request_date_from': request_date_from,
                    'request_date_to': request_date_to,
                    'name': name.strip() if name else (f'طلب إجازة نصف يوم ({lt.name})' if is_half_day else f'طلب إجازة ({lt.name}) من البوابة'),
                }
                if is_half_day:
                    leave_vals['number_of_days'] = 0.5
                    if 'request_unit_half' in request.env['hr.leave']._fields:
                        leave_vals['request_unit_half'] = True

            new_leave = request.env['hr.leave'].sudo().create(leave_vals)

            # Mobile Push Notification to direct supervisor
            if emp.parent_id:
                try:
                    req_label = f"إذن ({calc_hours:g} س)" if is_hourly_type else f"إجازة ({lt.name})"
                    request.env['jandalah.push.subscriber'].sudo().notify_employee(
                        employee=emp.parent_id,
                        title="طلب موظف جديد ⏳",
                        body=f"قدم الموظف {emp.name} طلب {req_label} بانتظار موافقتك.",
                        url="/my/team/leaves"
                    )
                except Exception as e_push:
                    _logger.warning("Error pushing leave request to supervisor: %s", e_push)

            # Handle file attachment (Medical report / excuse document)
            attachment_file = request.httprequest.files.get('attachment')
            if attachment_file and attachment_file.filename:
                file_content = attachment_file.read()
                if file_content:
                    att_vals = {
                        'name': attachment_file.filename,
                        'type': 'binary',
                        'datas': base64.b64encode(file_content),
                        'res_model': 'hr.leave',
                        'res_id': new_leave.id,
                        'mimetype': attachment_file.content_type or 'application/octet-stream',
                    }
                    att = request.env['ir.attachment'].sudo().create(att_vals)
                    write_vals = {}
                    if 'supported_attachment_ids' in new_leave._fields:
                        write_vals['supported_attachment_ids'] = [(4, att.id)]
                    if 'message_main_attachment_id' in new_leave._fields:
                        write_vals['message_main_attachment_id'] = att.id
                    if write_vals:
                        new_leave.sudo().write(write_vals)

                    try:
                        new_leave.sudo().message_post(
                            body=f"تم إرفاق مستند / تقرير طبي مع طلب الإجازة: {attachment_file.filename}",
                            attachment_ids=[att.id]
                        )
                    except Exception:
                        pass

            return request.redirect('/my/leaves?success=1')
        except Exception as e:
            return request.redirect(f'/my/leaves?error={str(e)}')

    @http.route('/my/leaves/attachment/<int:leave_id>', type='http', auth='user', website=True)
    def portal_leave_attachment(self, leave_id, **kw):
        """Allows employee and direct supervisor to view/download leave attachments securely."""
        emp = self._get_employee()
        if not emp:
            return request.redirect('/my/leaves')

        leave = request.env['hr.leave'].sudo().browse(leave_id)
        if not leave.exists():
            return request.not_found()

        # Authorization: Must be own leave or employee must be a direct subordinate
        subordinates = self._get_subordinates(emp)
        if leave.employee_id.id != emp.id and leave.employee_id.id not in subordinates.ids:
            return request.redirect('/my/leaves?error=unauthorized')

        # Find attachment
        att = None
        if 'supported_attachment_ids' in leave._fields and leave.supported_attachment_ids:
            att = leave.supported_attachment_ids[0]
        elif 'message_main_attachment_id' in leave._fields and leave.message_main_attachment_id:
            att = leave.message_main_attachment_id
        if not att:
            att = request.env['ir.attachment'].sudo().search([
                ('res_model', '=', 'hr.leave'),
                ('res_id', '=', leave.id)
            ], order='id desc', limit=1)

        if att:
            return request.redirect(f'/web/content/{att.id}')
        return request.redirect('/my/leaves?error=no_attachment')

    # =========================================================================
    # SUPERVISOR & DIRECT MANAGER PORTAL SUITE (Zero License Overhead)
    # =========================================================================

    @http.route('/my/team', type='http', auth='user', website=True)
    def portal_my_team_attendance(self, filter_type=None, date_from=None, date_to=None, active_tab='live', **kw):
        """Displays live attendance and periodic historical report of subordinates for supervisors/direct managers."""
        emp = self._get_employee()
        if not emp:
            return request.render('portal.portal_my_home', {'error_message': 'لا يوجد ملف موظف مرتبط'})

        subordinates = self._get_subordinates(emp)
        if not subordinates:
            return request.render('portal.portal_my_home', {
                'error_message': 'ليس لديك موظفون تابعون للإشراف الميداني أو الإدارة المباشرة'
            })

        Attendance = request.env['hr.attendance'].sudo()
        today = date.today()

        # 1. Resolve Date Range based on filter_type
        if not filter_type:
            filter_type = 'today'

        if filter_type == 'yesterday':
            d_start = today - timedelta(days=1)
            d_end = today - timedelta(days=1)
        elif filter_type == 'this_week':
            # Saudi work week starts Saturday (weekday 5) or Sunday (weekday 6)
            days_since_sat = (today.weekday() + 2) % 7
            d_start = today - timedelta(days=days_since_sat)
            d_end = today
        elif filter_type == 'this_month':
            d_start = today.replace(day=1)
            d_end = today
        elif filter_type == 'custom' and date_from and date_to:
            try:
                d_start = datetime.strptime(date_from, '%Y-%m-%d').date()
                d_end = datetime.strptime(date_to, '%Y-%m-%d').date()
            except Exception:
                d_start = today
                d_end = today
        else:
            filter_type = 'today'
            d_start = today
            d_end = today

        start_dt = datetime.combine(d_start, datetime.min.time())
        end_dt = datetime.combine(d_end, datetime.max.time())

        # 2. Live Today Status (Radar)
        team_data = []
        today_start = datetime.utcnow().replace(hour=0, minute=0, second=0, microsecond=0)

        for sub in subordinates:
            open_att = Attendance.search([
                ('employee_id', '=', sub.id),
                ('check_out', '=', False)
            ], order='check_in desc', limit=1)

            last_att = Attendance.search([
                ('employee_id', '=', sub.id)
            ], order='check_in desc', limit=1)

            today_atts = Attendance.search([
                ('employee_id', '=', sub.id),
                ('check_in', '>=', today_start)
            ])
            today_hours = round(sum(a.worked_hours for a in today_atts if a.worked_hours), 2)

            team_data.append({
                'employee': sub,
                'is_checked_in': bool(open_att),
                'open_attendance': open_att,
                'last_attendance': last_att,
                'today_hours': today_hours,
                'check_in_time_str': format_tz_dynamic(open_att.check_in) if open_att else (format_tz_dynamic(last_att.check_in) if last_att else '-'),
                'check_out_time_str': format_tz_dynamic(last_att.check_out) if last_att and last_att.check_out else ('متواجد بالعمل' if open_att else '-'),
            })

        checked_in_count = sum(1 for m in team_data if m['is_checked_in'])

        # 3. Periodic Attendance Report (History & Analytics for Date Range)
        period_data = []
        for sub in subordinates:
            period_atts = Attendance.search([
                ('employee_id', '=', sub.id),
                ('check_in', '>=', start_dt),
                ('check_in', '<=', end_dt)
            ], order='check_in desc')

            tot_hours = round(sum(a.worked_hours for a in period_atts if a.worked_hours), 2)
            days_attended = len(set(a.check_in.date() for a in period_atts if a.check_in))

            logs = []
            for a in period_atts:
                logs.append({
                    'id': a.id,
                    'date_str': a.check_in.strftime('%Y-%m-%d') if a.check_in else '-',
                    'day_name': a.check_in.strftime('%A') if a.check_in else '',
                    'check_in_str': format_tz_dynamic(a.check_in),
                    'check_out_str': format_tz_dynamic(a.check_out) if a.check_out else 'ما زال متواجداً',
                    'hours': round(a.worked_hours, 2) if a.worked_hours else 0.0,
                    'in_lat': a.in_latitude if hasattr(a, 'in_latitude') else False,
                    'in_lng': a.in_longitude if hasattr(a, 'in_longitude') else False,
                })

            period_data.append({
                'employee': sub,
                'total_hours': tot_hours,
                'days_attended': days_attended,
                'records_count': len(period_atts),
                'logs': logs,
            })

        period_total_hours = round(sum(p['total_hours'] for p in period_data), 2)
        period_avg_hours = round(period_total_hours / len(subordinates), 2) if subordinates else 0.0
        period_days_span = (d_end - d_start).days + 1

        values = self._prepare_portal_layout_values()
        values.update({
            'employee': emp,
            'team_data': team_data,
            'period_data': period_data,
            'total_subordinates': len(subordinates),
            'checked_in_count': checked_in_count,
            'period_total_hours': period_total_hours,
            'period_avg_hours': period_avg_hours,
            'period_days_span': period_days_span,
            'filter_type': filter_type,
            'date_from': d_start.strftime('%Y-%m-%d'),
            'date_to': d_end.strftime('%Y-%m-%d'),
            'active_tab': active_tab if active_tab in ['live', 'report'] else ('report' if filter_type != 'today' else 'live'),
            'page_name': 'team_attendance',
            'format_tz': format_tz_dynamic,
            'default_url': '/my/team',
        })
        return request.render('jandalah_employee_portal.portal_team_attendance_view', values)

    @http.route('/my/team/leaves', type='http', auth='user', website=True)
    def portal_my_team_leaves(self, success=None, error=None, **kw):
        """Displays pending leave requests submitted by subordinates for supervisor/manager approval."""
        emp = self._get_employee()
        if not emp:
            return request.redirect('/my?error=no_employee')

        subordinates = self._get_subordinates(emp)
        if not subordinates:
            values = self._prepare_portal_layout_values()
            values.update({
                'employee': emp,
                'pending_leaves': [],
                'history_leaves': [],
                'pending_count': 0,
                'page_name': 'team_leaves',
                'no_subordinates': True,
                'success': success,
                'error': error,
                'format_tz': format_tz_dynamic,
                'default_url': '/my/team/leaves',
            })
            return request.render('jandalah_employee_portal.portal_team_leaves_view', values)

        sub_ids = subordinates.ids
        Leave = request.env['hr.leave'].sudo()

        # Find leaves in confirm or validate1
        raw_pending = Leave.search([
            ('employee_id', 'in', sub_ids),
            ('state', 'in', ['confirm', 'validate1'])
        ], order='date_from desc')

        raw_history = Leave.search([
            ('employee_id', 'in', sub_ids),
            ('state', 'in', ['validate', 'refuse'])
        ], order='date_from desc', limit=30)

        # Pre-extract all data into clean Python dicts to eliminate QWeb crashes & AccessErrors
        pending_leaves_data = []
        for l in raw_pending:
            emp_rec = l.employee_id
            emp_name = emp_rec.name if emp_rec else 'موظف'
            avatar_char = emp_name[:1] if emp_name else 'م'
            job_title = emp_rec.job_title if emp_rec and emp_rec.job_title else 'موظف ميداني'
            leave_type = l.holiday_status_id.name if l.holiday_status_id else 'إجازة'

            has_attachment = False
            try:
                if 'supported_attachment_ids' in l._fields and l.supported_attachment_ids:
                    has_attachment = True
                elif 'message_main_attachment_id' in l._fields and l.message_main_attachment_id:
                    has_attachment = True
                elif request.env['ir.attachment'].sudo().search_count([('res_model', '=', 'hr.leave'), ('res_id', '=', l.id)]) > 0:
                    has_attachment = True
            except Exception:
                pass

            date_from_val = l.request_date_from or (l.date_from.date() if l.date_from else '')
            date_to_val = l.request_date_to or (l.date_to.date() if l.date_to else '')

            is_hourly_req = (
                getattr(l, 'request_unit_hours', False) or
                (hasattr(l, 'number_of_hours_display') and l.number_of_hours_display > 0 and l.number_of_hours_display < 8) or
                any(k in leave_type for k in ['إذن', 'اذن', 'استئذان', 'ساعي', 'خروج'])
            )
            hours_disp = ''
            if is_hourly_req:
                h_val = getattr(l, 'number_of_hours_display', 0.0) or (l.number_of_days * 8.0 if l.number_of_days < 1 else 3.0)
                h_val = min(h_val, 3.0)
                hours_disp = f"{int(h_val)} س" if h_val.is_integer() else f"{h_val:g} س"

            pending_leaves_data.append({
                'id': l.id,
                'employee_name': emp_name,
                'avatar_char': avatar_char,
                'job_title': job_title,
                'holiday_type': leave_type,
                'request_date_from': str(date_from_val or '-'),
                'request_date_to': str(date_to_val or '-'),
                'number_of_days': l.number_of_days or 1.0,
                'is_hourly': is_hourly_req,
                'hours_display': hours_disp,
                'name': l.name or '',
                'has_attachment': has_attachment,
            })

        history_leaves_data = []
        for l in raw_history:
            emp_rec = l.employee_id
            emp_name = emp_rec.name if emp_rec else 'موظف'
            job_title = emp_rec.job_title if emp_rec and emp_rec.job_title else 'موظف ميداني'
            leave_type = l.holiday_status_id.name if l.holiday_status_id else 'إجازة'

            has_attachment = False
            try:
                if 'supported_attachment_ids' in l._fields and l.supported_attachment_ids:
                    has_attachment = True
                elif 'message_main_attachment_id' in l._fields and l.message_main_attachment_id:
                    has_attachment = True
                elif request.env['ir.attachment'].sudo().search_count([('res_model', '=', 'hr.leave'), ('res_id', '=', l.id)]) > 0:
                    has_attachment = True
            except Exception:
                pass

            refusal_reason = ''
            if l.state == 'refuse':
                if 'report_note' in l._fields and l.report_note:
                    refusal_reason = l.report_note
                elif 'notes' in l._fields and l.notes:
                    refusal_reason = l.notes

            date_from_val = l.request_date_from or (l.date_from.date() if l.date_from else '')
            date_to_val = l.request_date_to or (l.date_to.date() if l.date_to else '')

            is_hourly_hist = (
                getattr(l, 'request_unit_hours', False) or
                (hasattr(l, 'number_of_hours_display') and l.number_of_hours_display > 0 and l.number_of_hours_display < 8) or
                any(k in leave_type for k in ['إذن', 'اذن', 'استئذان', 'ساعي', 'خروج'])
            )
            hours_disp_hist = ''
            if is_hourly_hist:
                h_val_hist = getattr(l, 'number_of_hours_display', 0.0) or (l.number_of_days * 8.0 if l.number_of_days < 1 else 3.0)
                h_val_hist = min(h_val_hist, 3.0)
                hours_disp_hist = f"{int(h_val_hist)} س" if h_val_hist.is_integer() else f"{h_val_hist:g} س"

            history_leaves_data.append({
                'id': l.id,
                'employee_name': emp_name,
                'job_title': job_title,
                'holiday_type': leave_type,
                'request_date_from': str(date_from_val or '-'),
                'request_date_to': str(date_to_val or '-'),
                'number_of_days': l.number_of_days or 1.0,
                'is_hourly': is_hourly_hist,
                'hours_display': hours_disp_hist,
                'state': l.state,
                'refusal_reason': refusal_reason,
                'has_attachment': has_attachment,
            })

        values = self._prepare_portal_layout_values()
        values.update({
            'employee': emp,
            'pending_leaves': pending_leaves_data,
            'history_leaves': history_leaves_data,
            'pending_count': len(pending_leaves_data),
            'page_name': 'team_leaves',
            'no_subordinates': False,
            'success': success,
            'error': error,
            'format_tz': format_tz_dynamic,
            'default_url': '/my/team/leaves',
        })
        return request.render('jandalah_employee_portal.portal_team_leaves_view', values)

    @http.route('/my/team/leaves/action', type='http', auth='user', methods=['POST'], website=True, csrf=True)
    def portal_team_leave_action(self, leave_id=None, action=None, refusal_reason=None, **kw):
        """Executes Approve or Refuse on subordinate leave requests with mandatory refusal notes."""
        emp = self._get_employee()
        if not emp or not leave_id or action not in ['approve', 'refuse']:
            return request.redirect('/my/team/leaves?error=invalid_request')

        subordinates = self._get_subordinates(emp)
        sub_ids = subordinates.ids

        leave = request.env['hr.leave'].sudo().browse(int(leave_id))
        if not leave.exists():
            return request.redirect('/my/team/leaves?error=not_found')

        user = request.env.user
        is_hr_admin = (
            user.has_group('hr_holidays.group_hr_holidays_user') or
            user.has_group('hr_holidays.group_hr_holidays_manager') or
            user.has_group('base.group_system') or
            user._is_admin()
        )
        if leave.employee_id.id not in sub_ids and not is_hr_admin:
            return request.redirect('/my/team/leaves?error=unauthorized')

        try:
            # Auto-heal: Ensure leave_manager_id is set to supervisor's user if missing
            if leave.employee_id and not leave.employee_id.leave_manager_id:
                sup_user = leave.employee_id.parent_id.user_id if leave.employee_id.parent_id else None
                if sup_user:
                    try:
                        leave.employee_id.sudo().write({'leave_manager_id': sup_user.id})
                    except Exception:
                        pass

            admin_leave = leave.sudo().with_user(SUPERUSER_ID)
            if action == 'approve':
                val_type = getattr(leave.holiday_status_id, 'leave_validation_type', 'manager')
                if is_hr_admin:
                    # Direct HR Approval: Validates directly to final state, bypassing supervisor if needed
                    if hasattr(admin_leave, 'action_validate'):
                        admin_leave.action_validate()
                    elif hasattr(admin_leave, 'action_approve'):
                        admin_leave.action_approve()
                    else:
                        admin_leave.write({'state': 'validate'})
                    try:
                        admin_leave.message_post(
                            body=f"تم الاعتماد النهائي للطلب من قبل إدارة الموارد البشرية ({emp.name})."
                        )
                    except Exception:
                        pass
                else:
                    # Supervisor Approval: Moves to validate1 (pending HR approval)
                    if hasattr(admin_leave, 'action_approve'):
                        admin_leave.action_approve()
                    else:
                        new_st = 'validate1' if val_type == 'both' else 'validate'
                        admin_leave.write({'state': new_st})
                    try:
                        admin_leave.message_post(
                            body=f"تمت موافقة المشرف المباشر ({emp.name})، وبانتظار الاعتماد النهائي من الموارد البشرية."
                        )
                    except Exception:
                        pass

                try:
                    req_type = leave.holiday_status_id.name or "إجازة/إذن"
                    st_msg = "تم اعتماد طلبك نهائياً ✅" if leave.state == 'validate' else "تم اعتماد طلبك مبدئياً وبانتظار الموارد البشرية ⏳"
                    request.env['jandalah.push.subscriber'].sudo().notify_employee(
                        employee=leave.employee_id,
                        title="تحديث حالة طلبك 📋",
                        body=f"{st_msg} ({req_type}).",
                        url="/my/leaves"
                    )
                except Exception as e_push_app:
                    _logger.warning("Error pushing approval to employee: %s", e_push_app)

                return request.redirect('/my/team/leaves?success=approved')
            elif action == 'refuse':
                reason = (refusal_reason or kw.get('reason') or '').strip()
                write_vals = {}
                if 'report_note' in leave._fields:
                    write_vals['report_note'] = reason
                if 'notes' in leave._fields:
                    write_vals['notes'] = reason
                if write_vals:
                    admin_leave.write(write_vals)

                try:
                    admin_leave.message_post(
                        body=f"تم رفض طلب الإجازة من قبل المشرف المباشر ({emp.name}). سبب الرفض: {reason or 'لم يحدد'}"
                    )
                except Exception:
                    pass

                if hasattr(admin_leave, 'action_refuse'):
                    admin_leave.action_refuse()

                try:
                    req_type = leave.holiday_status_id.name or "إجازة/إذن"
                    request.env['jandalah.push.subscriber'].sudo().notify_employee(
                        employee=leave.employee_id,
                        title="تم رفض طلبك ❌",
                        body=f"تم رفض طلبك ({req_type}). سبب الرفض: {reason or 'لم يحدد'}",
                        url="/my/leaves"
                    )
                except Exception as e_push_res:
                    _logger.warning("Error pushing refusal to employee: %s", e_push_res)

                return request.redirect('/my/team/leaves?success=refused')
        except Exception as e:
            _logger.error("Error executing supervisor leave action: %s", e)
            return request.redirect(f'/my/team/leaves?error={str(e)}')


    # =========================================================================
    # 6. Announcements & Circulars (/my/announcements)
    # =========================================================================
    @http.route(['/my/announcements'], type='http', auth='user', website=True)
    def portal_my_announcements(self, filter='all', success=None, error=None, **kw):
        """Displays corporate announcements, circulars, and decisions for the employee."""
        emp = self._get_employee()
        if not emp:
            return request.redirect('/my?error=no_employee')

        Ann = request.env['jandalah.announcement'].sudo()
        Ack = request.env['jandalah.announcement.ack'].sudo()

        all_records = Ann.search([('active', '=', True)], order='date desc, id desc')
        my_acked_ids = set(Ack.search([('employee_id', '=', emp.id)]).mapped('announcement_id.id'))

        announcements_data = []
        unread_count = sum(1 for a in all_records if a.is_visible_for(emp) and a.id not in my_acked_ids)
        for a in all_records:
            if not a.is_visible_for(emp):
                continue
            is_read = a.id in my_acked_ids
            if filter == 'unread' and is_read:
                continue
            elif filter == 'read' and not is_read:
                continue

            issued_by_val = getattr(a, 'issued_by', False) or (a.create_uid.name if a.create_uid and a.create_uid.name != 'OdooBot' else 'الإدارة العامة')
            att_info = a.get_official_attachment_info()
            announcements_data.append({
                'id': a.id,
                'name': a.name,
                'announcement_type': a.announcement_type,
                'date': str(a.date or ''),
                'issued_by': issued_by_val,
                'content': a.content or '',
                'has_attachment': att_info['has_attachment'],
                'attachment_id': a.attachment_id.id if a.attachment_id else None,
                'attachment_name': att_info['name'] or 'document.pdf',
                'is_read': is_read,
                'ack_count': a.ack_count,
            })

        values = self._prepare_portal_layout_values()
        values.update({
            'employee': emp,
            'announcements': announcements_data,
            'active_filter': filter,
            'unread_count': unread_count,
            'page_name': 'announcements',
            'success': success,
            'error': error,
            'format_tz': format_tz_dynamic,
            'default_url': '/my/announcements',
        })
        return request.render('jandalah_employee_portal.portal_my_announcements_view', values)

    @http.route('/my/announcements/ack/<int:announcement_id>', type='http', auth='user', methods=['POST'], website=True, csrf=True)
    def portal_announcement_ack(self, announcement_id, **kw):
        """Records employee electronic acknowledgment of reading a circular/letter."""
        emp = self._get_employee()
        if not emp:
            return request.redirect('/my?error=no_employee')

        ann = request.env['jandalah.announcement'].sudo().browse(int(announcement_id))
        if not ann.exists() or not ann.is_visible_for(emp):
            return request.redirect('/my/announcements?error=not_found')

        Ack = request.env['jandalah.announcement.ack'].sudo()
        existing = Ack.search([('announcement_id', '=', ann.id), ('employee_id', '=', emp.id)], limit=1)
        if not existing:
            try:
                Ack.create({
                    'announcement_id': ann.id,
                    'employee_id': emp.id,
                    'ack_date': fields.Datetime.now(),
                })
            except Exception as e:
                _logger.error("Error saving announcement ack: %s", e)

        return request.redirect('/my/announcements?success=acknowledged')

    @http.route('/my/announcements/download/<int:announcement_id>', type='http', auth='user', website=True)
    def portal_announcement_download(self, announcement_id, **kw):
        """Securely serves the official announcement attachment file to authorized employees."""
        emp = self._get_employee()
        if not emp:
            return request.redirect('/my/announcements')

        ann = request.env['jandalah.announcement'].sudo().browse(announcement_id)
        if not ann.exists() or not ann.is_visible_for(emp):
            return request.not_found()

        att_info = ann.get_official_attachment_info()
        if not att_info['has_attachment']:
            return request.redirect('/my/announcements?error=no_attachment')

        file_data = None
        if att_info.get('id'):
            try:
                att = request.env['ir.attachment'].sudo().with_context(bin_size=False).browse(att_info['id'])
                if att.exists():
                    file_data = att.raw
                    if not file_data and att.datas:
                        try:
                            file_data = base64.b64decode(att.datas)
                        except Exception:
                            file_data = att.datas
                    if not file_data and getattr(att, 'store_fname', False):
                        try:
                            file_data = att._file_read(att.store_fname)
                        except Exception:
                            pass
            except Exception as e_att:
                _logger.warning("Error fetching announcement attachment raw bytes: %s", e_att)

        if not file_data and att_info.get('data'):
            try:
                d = att_info['data']
                if isinstance(d, str):
                    file_data = base64.b64decode(d)
                elif isinstance(d, bytes):
                    try:
                        file_data = base64.b64decode(d)
                    except Exception:
                        file_data = d
            except Exception as e_dec:
                _logger.error("Failed to decode announcement file data: %s", e_dec)

        if not file_data:
            return request.redirect('/my/announcements?error=invalid_file')

        filename = att_info['name'] or f"announcement_{ann.id}.pdf"
        if '.' not in filename:
            filename = f"{filename}.pdf"

        content_type = 'application/pdf'
        if filename.lower().endswith(('.jpg', '.jpeg')):
            content_type = 'image/jpeg'
        elif filename.lower().endswith('.png'):
            content_type = 'image/png'
        elif not filename.lower().endswith('.pdf'):
            content_type = 'application/octet-stream'

        ascii_filename = f"announcement_{ann.id}.pdf"
        encoded_filename = urllib.parse.quote(filename)
        headers = [
            ('Content-Type', content_type),
            ('Content-Disposition', f'attachment; filename="{ascii_filename}"; filename*=UTF-8\'\'{encoded_filename}'),
            ('Content-Length', len(file_data)),
        ]
        return request.make_response(file_data, headers=headers)


    # =========================================================================
    # 7. Penalties & Investigations (/my/penalties)
    # =========================================================================
    @http.route(['/my/penalties'], type='http', auth='user', website=True)
    def portal_my_penalties(self, success=None, error=None, **kw):
        """Displays disciplinary actions, deductions, warnings, and investigations."""
        emp = self._get_employee()
        if not emp:
            return request.redirect('/my?error=no_employee')

        Penalty = request.env['jandalah.hr.penalty'].sudo()
        records = Penalty.search([('employee_id', '=', emp.id)], order='date desc, id desc')

        penalties_data = []
        for p in records:
            issued_by_val = getattr(p, 'issued_by', False) or (p.create_uid.name if p.create_uid and p.create_uid.name != 'OdooBot' else 'إدارة الموارد البشرية')
            att_info = p.get_official_attachment_info()
            penalties_data.append({
                'id': p.id,
                'name': p.name,
                'penalty_type': p.penalty_type,
                'date': str(p.date or ''),
                'issued_by': issued_by_val,
                'reason': p.reason or '',
                'deduction_days': p.deduction_days or 0.0,
                'state': p.state,
                'employee_response': p.employee_response or '',
                'employee_comment': p.employee_comment or '',
                'response_date': str(p.response_date or ''),
                'has_attachment': att_info['has_attachment'],
                'attachment_name': att_info['name'] or 'document.pdf',
                'attachment_id': p.attachment_id.id if p.attachment_id else None,
            })

        values = self._prepare_portal_layout_values()
        values.update({
            'employee': emp,
            'penalties': penalties_data,
            'page_name': 'penalties',
            'success': success,
            'error': error,
            'format_tz': format_tz_dynamic,
            'default_url': '/my/penalties',
        })
        return request.render('jandalah_employee_portal.portal_my_penalties_view', values)

    @http.route('/my/penalties/respond', type='http', auth='user', methods=['POST'], website=True, csrf=True)
    def portal_penalty_respond(self, penalty_id=None, response_type=None, employee_comment=None, **kw):
        """Allows employee to accept or object to a penalty/investigation with mandatory statement."""
        emp = self._get_employee()
        if not emp:
            return request.redirect('/my?error=no_employee')

        if not penalty_id or not response_type or response_type not in ['accept', 'object']:
            return request.redirect('/my/penalties?error=invalid_data')

        penalty = request.env['jandalah.hr.penalty'].sudo().browse(int(penalty_id))
        if not penalty.exists() or penalty.employee_id.id != emp.id:
            return request.redirect('/my/penalties?error=unauthorized')

        comment = (employee_comment or '').strip()
        new_state = 'accepted' if response_type == 'accept' else 'objected'

        try:
            penalty.sudo().write({
                'employee_response': response_type,
                'employee_comment': comment,
                'state': new_state,
                'response_date': fields.Datetime.now(),
            })
            return request.redirect('/my/penalties?success=responded')
        except Exception as e:
            _logger.error("Error recording penalty response: %s", e)
            return request.redirect(f'/my/penalties?error={str(e)}')

    @http.route('/my/penalties/download/<int:penalty_id>', type='http', auth='user', website=True)
    def portal_penalty_download(self, penalty_id, **kw):
        """Securely serves penalty/investigation documents to the authorized employee."""
        emp = self._get_employee()
        if not emp:
            return request.redirect('/my/penalties')

        p = request.env['jandalah.hr.penalty'].sudo().browse(penalty_id)
        if not p.exists() or p.employee_id.id != emp.id:
            return request.not_found()

        att_info = p.get_official_attachment_info()
        if not att_info['has_attachment']:
            return request.redirect('/my/penalties?error=no_attachment')

        file_data = None
        if att_info.get('id'):
            try:
                att = request.env['ir.attachment'].sudo().with_context(bin_size=False).browse(att_info['id'])
                if att.exists():
                    file_data = att.raw
                    if not file_data and att.datas:
                        try:
                            file_data = base64.b64decode(att.datas)
                        except Exception:
                            file_data = att.datas
                    if not file_data and getattr(att, 'store_fname', False):
                        try:
                            file_data = att._file_read(att.store_fname)
                        except Exception:
                            pass
            except Exception as e_att:
                _logger.warning("Error fetching penalty attachment raw bytes: %s", e_att)

        if not file_data and att_info.get('data'):
            try:
                d = att_info['data']
                if isinstance(d, str):
                    file_data = base64.b64decode(d)
                elif isinstance(d, bytes):
                    try:
                        file_data = base64.b64decode(d)
                    except Exception:
                        file_data = d
            except Exception as e_dec:
                _logger.error("Failed to decode penalty file data: %s", e_dec)

        if not file_data:
            return request.redirect('/my/penalties?error=invalid_file')

        filename = att_info['name'] or f"penalty_{p.id}.pdf"
        if '.' not in filename:
            filename = f"{filename}.pdf"

        content_type = 'application/pdf'
        if filename.lower().endswith(('.jpg', '.jpeg')):
            content_type = 'image/jpeg'
        elif filename.lower().endswith('.png'):
            content_type = 'image/png'
        elif not filename.lower().endswith('.pdf'):
            content_type = 'application/octet-stream'

        ascii_filename = f"penalty_{p.id}.pdf"
        encoded_filename = urllib.parse.quote(filename)
        headers = [
            ('Content-Type', content_type),
            ('Content-Disposition', f'attachment; filename="{ascii_filename}"; filename*=UTF-8\'\'{encoded_filename}'),
            ('Content-Length', len(file_data)),
        ]
        return request.make_response(file_data, headers=headers)


    # =========================================================================
    # 8. Assets & Custody (/my/assets)
    # =========================================================================
    @http.route(['/my/assets'], type='http', auth='user', website=True)
    def portal_my_assets(self, success=None, error=None, **kw):
        """Displays company assets and custody assigned to the employee."""
        emp = self._get_employee()
        if not emp:
            return request.redirect('/my?error=no_employee')

        Asset = request.env['jandalah.hr.asset'].sudo()
        records = Asset.search([('employee_id', '=', emp.id)], order='handover_date desc, id desc')

        assets_data = []
        for a in records:
            cat_name = a.category_id.name if a.category_id else (
                dict(a._fields['category'].selection).get(a.category, 'عهدة أخرى') if a.category else 'عهدة أخرى'
            )
            assets_data.append({
                'id': a.id,
                'name': a.name,
                'asset_code': a.asset_code or '-',
                'category': a.category,
                'category_name': cat_name,
                'handover_date': str(a.handover_date or ''),
                'return_date': str(a.return_date or '-'),
                'notes': a.notes or '',
                'state': a.state,
                'ack_date': str(a.ack_date or ''),
                'has_attachment': bool(a.attachment_file),
                'attachment_name': a.attachment_name or f"asset_{a.id}.pdf",
                'employee_feedback': a.employee_feedback or '',
                'return_notes': a.return_notes or '',
                'return_request_date': str(a.return_request_date or ''),
            })

        values = self._prepare_portal_layout_values()
        values.update({
            'employee': emp,
            'assets': assets_data,
            'page_name': 'assets',
            'success': success,
            'error': error,
            'format_tz': format_tz_dynamic,
            'default_url': '/my/assets',
        })
        return request.render('jandalah_employee_portal.portal_my_assets_view', values)

    @http.route('/my/assets/ack/<int:asset_id>', type='http', auth='user', methods=['POST'], website=True, csrf=True)
    def portal_asset_ack(self, asset_id, **kw):
        """Employee electronically confirms receipt of an assigned asset."""
        emp = self._get_employee()
        if not emp:
            return request.redirect('/my?error=no_employee')

        asset = request.env['jandalah.hr.asset'].sudo().browse(int(asset_id))
        if not asset.exists() or asset.employee_id.id != emp.id:
            return request.redirect('/my/assets?error=unauthorized')

        try:
            feedback = (kw.get('employee_feedback') or '').strip()
            asset.sudo().write({
                'state': 'acknowledged',
                'ack_date': fields.Datetime.now(),
                'ack_signature': f"تم الإقرار إلكترونياً بواسطة: {emp.name}",
                'employee_feedback': feedback,
            })
            return request.redirect('/my/assets?success=acknowledged')
        except Exception as e:
            _logger.error("Error acknowledging asset: %s", e)
            return request.redirect(f'/my/assets?error={str(e)}')

    @http.route('/my/assets/download/<int:asset_id>', type='http', auth='user', website=True)
    def portal_asset_download(self, asset_id, **kw):
        """Allows employee to download official asset attachment."""
        emp = self._get_employee()
        if not emp:
            return request.not_found()
        asset = request.env['jandalah.hr.asset'].sudo().browse(int(asset_id))
        if not asset.exists() or asset.employee_id.id != emp.id:
            return request.not_found()
        if asset.attachment_file:
            import base64
            file_data = base64.b64decode(asset.attachment_file)
            filename = asset.attachment_name or f"asset_{asset.id}.pdf"
            return request.make_response(file_data, [
                ('Content-Type', 'application/octet-stream'),
                ('Content-Disposition', f'attachment; filename="{filename}"')
            ])
        return request.not_found()

    @http.route('/my/assets/return/<int:asset_id>', type='http', auth='user', methods=['POST'], website=True, csrf=True)
    def portal_asset_return_request(self, asset_id, return_notes=None, **kw):
        """Allows employee to initiate asset return with condition notes."""
        emp = self._get_employee()
        if not emp:
            return request.redirect('/my?error=no_employee')
        asset = request.env['jandalah.hr.asset'].sudo().browse(int(asset_id))
        if not asset.exists() or asset.employee_id.id != emp.id:
            return request.redirect('/my/assets?error=unauthorized')
        try:
            asset.sudo().write({
                'state': 'return_requested',
                'return_request_date': fields.Datetime.now(),
                'return_notes': (return_notes or '').strip(),
            })
            return request.redirect('/my/assets?success=return_requested')
        except Exception as e:
            return request.redirect(f'/my/assets?error={str(e)}')


    # =========================================================================
    # 9. Temporary Work Location Requests (/my/location-requests)
    # =========================================================================
    @http.route(['/my/location-requests'], type='http', auth='user', website=True)
    def portal_my_location_requests(self, success=None, error=None, **kw):
        """Displays temporary work location requests and lets employee submit new ones."""
        emp = self._get_employee()
        if not emp:
            return request.redirect('/my?error=no_employee')

        Req = request.env['jandalah.location.request'].sudo()
        records = Req.search([('employee_id', '=', emp.id)], order='date_from desc, id desc')

        requests_data = []
        for r in records:
            requests_data.append({
                'id': r.id,
                'temp_location_name': r.temp_location_name,
                'date_from': str(r.date_from or ''),
                'date_to': str(r.date_to or ''),
                'reason': r.reason or '',
                'state': r.state,
                'refusal_reason': r.refusal_reason or '',
                'temp_radius_meters': r.temp_radius_meters or 500,
            })

        # Effective current work location
        effective_loc = {}
        try:
            if hasattr(emp.sudo(), 'get_effective_work_location'):
                effective_loc = emp.sudo().get_effective_work_location()
        except Exception as e:
            _logger.error("Error retrieving effective work location: %s", e)

        values = self._prepare_portal_layout_values()
        values.update({
            'employee': emp,
            'location_requests': requests_data,
            'effective_loc': effective_loc,
            'page_name': 'location_requests',
            'success': success,
            'error': error,
            'format_tz': format_tz_dynamic,
            'default_url': '/my/location-requests',
        })
        return request.render('jandalah_employee_portal.portal_my_location_requests_view', values)

    @http.route('/my/location-requests/submit', type='http', auth='user', methods=['POST'], website=True, csrf=True)
    def portal_location_request_submit(self, date_from=None, date_to=None, temp_location_name=None, reason=None, temp_latitude=None, temp_longitude=None, **kw):
        """Handles submission of a new temporary location exception request."""
        emp = self._get_employee()
        if not emp:
            return request.redirect('/my?error=no_employee')

        if not date_from or not date_to or not temp_location_name or not reason:
            return request.redirect('/my/location-requests?error=missing_fields')

        lat_val = 0.0
        lon_val = 0.0
        try:
            if temp_latitude:
                lat_val = float(temp_latitude)
            if temp_longitude:
                lon_val = float(temp_longitude)
        except (ValueError, TypeError):
            pass

        try:
            request.env['jandalah.location.request'].sudo().create({
                'employee_id': emp.id,
                'date_from': date_from,
                'date_to': date_to,
                'temp_location_name': temp_location_name.strip(),
                'reason': reason.strip(),
                'temp_latitude': lat_val,
                'temp_longitude': lon_val,
                'temp_radius_meters': 500,
                'state': 'confirm',
            })
            return request.redirect('/my/location-requests?success=submitted')
        except Exception as e:
            _logger.error("Error submitting location request: %s", e)
            return request.redirect(f'/my/location-requests?error={str(e)}')

    @http.route(['/my/team/location-requests'], type='http', auth='user', website=True)
    def portal_team_location_requests(self, success=None, error=None, **kw):
        """Displays team temporary location requests for supervisor approval."""
        emp = self._get_employee()
        if not emp:
            return request.redirect('/my?error=no_employee')

        subordinates = self._get_subordinates(emp)
        if not subordinates:
            return request.redirect('/my?error=no_subordinates')

        sub_ids = subordinates.ids
        Req = request.env['jandalah.location.request'].sudo()

        pending_recs = Req.search([('employee_id', 'in', sub_ids), ('state', '=', 'confirm')], order='date_from desc')
        history_recs = Req.search([('employee_id', 'in', sub_ids), ('state', 'in', ['approve', 'refuse'])], order='date_from desc', limit=20)

        pending_data = []
        for r in pending_recs:
            pending_data.append({
                'id': r.id,
                'employee_name': r.employee_id.name,
                'temp_location_name': r.temp_location_name,
                'date_from': str(r.date_from or ''),
                'date_to': str(r.date_to or ''),
                'reason': r.reason or '',
                'state': r.state,
            })

        history_data = []
        for r in history_recs:
            history_data.append({
                'id': r.id,
                'employee_name': r.employee_id.name,
                'temp_location_name': r.temp_location_name,
                'date_from': str(r.date_from or ''),
                'date_to': str(r.date_to or ''),
                'state': r.state,
                'refusal_reason': r.refusal_reason or '',
            })

        values = self._prepare_portal_layout_values()
        values.update({
            'employee': emp,
            'pending_requests': pending_data,
            'history_requests': history_data,
            'pending_count': len(pending_data),
            'page_name': 'team_location_requests',
            'success': success,
            'error': error,
            'format_tz': format_tz_dynamic,
            'default_url': '/my/team/location-requests',
        })
        return request.render('jandalah_employee_portal.portal_team_location_requests_view', values)

    @http.route('/my/team/location-requests/action', type='http', auth='user', methods=['POST'], website=True, csrf=True)
    def portal_team_location_request_action(self, request_id=None, action=None, refusal_reason=None, **kw):
        """Supervisor approves or refuses a team member temporary location request."""
        emp = self._get_employee()
        if not emp:
            return request.redirect('/my?error=no_employee')

        subordinates = self._get_subordinates(emp)
        sub_ids = subordinates.ids

        req_rec = request.env['jandalah.location.request'].sudo().browse(int(request_id))
        if not req_rec.exists() or req_rec.employee_id.id not in sub_ids:
            return request.redirect('/my/team/location-requests?error=unauthorized')

        try:
            if action == 'approve':
                req_rec.sudo().write({
                    'state': 'approve',
                    'supervisor_id': emp.id,
                    'action_date': fields.Datetime.now(),
                })
                return request.redirect('/my/team/location-requests?success=approved')
            elif action == 'refuse':
                req_rec.sudo().write({
                    'state': 'refuse',
                    'supervisor_id': emp.id,
                    'action_date': fields.Datetime.now(),
                    'refusal_reason': (refusal_reason or '').strip(),
                })
                return request.redirect('/my/team/location-requests?success=refused')
        except Exception as e:
            _logger.error("Error executing supervisor location action: %s", e)
            return request.redirect(f'/my/team/location-requests?error={str(e)}')
