# -*- coding: utf-8 -*-
from odoo import http, fields, _
from odoo.http import request
from odoo.addons.portal.controllers.portal import CustomerPortal, pager as portal_pager
import json
import pytz
from datetime import datetime, timedelta
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

def format_tz_dynamic(dt, tz_name=None):
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
        return f"{local_dt.strftime('%Y/%m/%d %I:%M')} {am_pm}"
    except Exception:
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
        # Helper to find the employee record linked to current user with comprehensive fallbacks
        user = request.env.user
        emp = request.env['hr.employee'].sudo().search([('user_id', '=', user.id)], limit=1)
        if not emp:
            user_email = (user.email or '').strip()
            user_login = (user.login or '').strip()
            # Build search domain for work_email, login, private_email, or partner_id
            domain = ['|', '|', '|',
                ('work_email', '=ilike', user_email),
                ('work_email', '=ilike', user_login),
                ('private_email', '=ilike', user_email),
                ('work_contact_id', '=', user.partner_id.id),
            ]
            emp = request.env['hr.employee'].sudo().search(domain, limit=1)
            if emp and not emp.user_id:
                emp.sudo().write({'user_id': user.id})

        # Dynamically sync detected timezone to employee & user
        if emp:
            detected_tz = get_current_user_tz()
            if detected_tz:
                if emp.tz != detected_tz:
                    emp.sudo().write({'tz': detected_tz})
                if user and user.tz != detected_tz:
                    user.sudo().write({'tz': detected_tz})

        return emp

    def _get_subordinates(self, emp):
        # Finds all subordinates where the current employee is Direct Manager (parent_id) or Coach/Supervisor (coach_id)
        if not emp:
            return request.env['hr.employee'].sudo().browse()
        return request.env['hr.employee'].sudo().search([
            '|', ('parent_id', '=', emp.id), ('coach_id', '=', emp.id)
        ])

    def _prepare_home_portal_values(self, counters):
        values = super()._prepare_home_portal_values(counters)
        emp = self._get_employee()
        if emp:
            open_att = request.env['hr.attendance'].sudo().search([
                ('employee_id', '=', emp.id),
                ('check_out', '=', False)
            ], order='check_in desc', limit=1)
            
            last_att = request.env['hr.attendance'].sudo().search([
                ('employee_id', '=', emp.id)
            ], order='check_in desc', limit=1)

            # Check supervisor/manager status
            subordinates = self._get_subordinates(emp)
            is_supervisor = bool(subordinates)
            team_checked_in = 0
            team_pending_leaves = 0

            if is_supervisor:
                sub_ids = subordinates.ids
                # Team members currently checked in
                team_checked_in = request.env['hr.attendance'].sudo().search_count([
                    ('employee_id', 'in', sub_ids),
                    ('check_out', '=', False)
                ])
                # Team leave requests awaiting approval
                team_pending_leaves = request.env['hr.leave'].sudo().search_count([
                    ('employee_id', 'in', sub_ids),
                    ('state', '=', 'confirm')
                ])

            values.update({
                'employee': emp,
                'is_checked_in': bool(open_att),
                'open_attendance': open_att,
                'last_attendance': last_att,
                'attendance_count': request.env['hr.attendance'].sudo().search_count([('employee_id', '=', emp.id)]),
                'leaves_count': request.env['hr.leave'].sudo().search_count([('employee_id', '=', emp.id)]),
                'format_tz': format_tz_dynamic,
                # Supervisor values
                'is_supervisor': is_supervisor,
                'subordinates_count': len(subordinates),
                'team_checked_in_count': team_checked_in,
                'team_pending_leaves_count': team_pending_leaves,
            })
        return values

    @http.route('/jandalah/manifest.json', type='http', auth='public', methods=['GET'], csrf=False)
    def portal_pwa_manifest(self, **kw):
        manifest_data = {
            "name": "بوابة موظفي جندله",
            "short_name": "جندله",
            "start_url": "/my",
            "scope": "/",
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

    @http.route('/jandalah/sw.js', type='http', auth='public', methods=['GET'], csrf=False)
    def portal_pwa_sw(self, **kw):
        sw_code = """
const CACHE_NAME = 'jandalah-ess-v2';
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
"""
        return request.make_response(
            sw_code.strip(),
            headers=[
                ('Content-Type', 'application/javascript; charset=utf-8'),
                ('Service-Worker-Allowed', '/'),
                ('Cache-Control', 'no-cache'),
            ]
        )

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

        # Check for open attendance
        open_att = request.env['hr.attendance'].sudo().search([
            ('employee_id', '=', emp.id),
            ('check_out', '=', False)
        ], order='check_in desc', limit=1)

        if open_att:
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
            # CHECK IN LOCALLY
            in_vals = {
                'employee_id': emp.id,
                'check_in': now_utc,
                'in_ip_address': ip_addr or 'N/A',
                'in_browser': browser or 'Mobile/Web Portal',
            }
            if latitude and longitude:
                try:
                    in_vals['in_latitude'] = float(latitude)
                    in_vals['in_longitude'] = float(longitude)
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
                'error_message': 'لا يوجد ملف موظف مرتبط'
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

        open_att = Attendance.search([
            ('employee_id', '=', emp.id),
            ('check_out', '=', False)
        ], order='check_in desc', limit=1)

        all_emp_att = Attendance.search(domain)
        total_hours = round(sum(a.worked_hours for a in all_emp_att if a.worked_hours), 2)

        values = self._prepare_portal_layout_values()
        values.update({
            'employee': emp,
            'attendances': attendances,
            'open_attendance': open_att,
            'is_checked_in': bool(open_att),
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
        leaves = Leave.search(domain, order='request_date_from desc', limit=items_per_page, offset=offset)
        leave_types = request.env['hr.leave.type'].sudo().search([('active', '=', True)])

        values = self._prepare_portal_layout_values()
        values.update({
            'employee': emp,
            'leaves': leaves,
            'leave_types': leave_types,
            'page_name': 'leaves',
            'pager': pager,
            'success': success,
            'error': error,
            'format_tz': format_tz_dynamic,
            'default_url': '/my/leaves',
        })
        return request.render('jandalah_employee_portal.portal_my_leaves_view', values)

    @http.route('/my/leaves/submit', type='http', auth='user', methods=['POST'], website=True, csrf=True)
    def portal_leave_submit(self, holiday_status_id=None, request_date_from=None, request_date_to=None, name=None, **kw):
        emp = self._get_employee()
        if not emp:
            return request.redirect('/my/leaves?error=no_employee')

        if not holiday_status_id or not request_date_from or not request_date_to:
            return request.redirect('/my/leaves?error=missing_fields')

        try:
            leave_vals = {
                'holiday_status_id': int(holiday_status_id),
                'employee_id': emp.id,
                'request_date_from': request_date_from,
                'request_date_to': request_date_to,
                'name': name.strip() if name else 'طلب إجازة من البوابة',
            }
            new_leave = request.env['hr.leave'].sudo().create(leave_vals)
            return request.redirect('/my/leaves?success=1')
        except Exception as e:
            return request.redirect(f'/my/leaves?error={str(e)}')

    # =========================================================================
    # SUPERVISOR & DIRECT MANAGER PORTAL SUITE (Zero License Overhead)
    # =========================================================================

    @http.route('/my/team', type='http', auth='user', website=True)
    def portal_my_team_attendance(self, **kw):
        """Displays live attendance and status of subordinates for supervisors/direct managers."""
        emp = self._get_employee()
        if not emp:
            return request.render('portal.portal_my_home', {'error_message': 'لا يوجد ملف موظف مرتبط'})

        subordinates = self._get_subordinates(emp)
        if not subordinates:
            return request.render('portal.portal_my_home', {
                'error_message': 'ليس لديك موظفون تابعون للإشراف الميداني أو الإدارة المباشرة'
            })

        Attendance = request.env['hr.attendance'].sudo()
        team_data = []

        for sub in subordinates:
            open_att = Attendance.search([
                ('employee_id', '=', sub.id),
                ('check_out', '=', False)
            ], order='check_in desc', limit=1)

            last_att = Attendance.search([
                ('employee_id', '=', sub.id)
            ], order='check_in desc', limit=1)

            today_start = datetime.utcnow().replace(hour=0, minute=0, second=0, microsecond=0)
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
                'check_in_time_str': format_tz_saudi(open_att.check_in) if open_att else (format_tz_saudi(last_att.check_in) if last_att else '-'),
                'check_out_time_str': format_tz_saudi(last_att.check_out) if last_att and last_att.check_out else ('متواجد بالعمل' if open_att else '-'),
            })

        checked_in_count = sum(1 for m in team_data if m['is_checked_in'])

        values = self._prepare_portal_layout_values()
        values.update({
            'employee': emp,
            'team_data': team_data,
            'total_subordinates': len(subordinates),
            'checked_in_count': checked_in_count,
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
            return request.render('portal.portal_my_home', {'error_message': 'لا يوجد ملف موظف مرتبط'})

        subordinates = self._get_subordinates(emp)
        if not subordinates:
            return request.render('portal.portal_my_home', {
                'error_message': 'ليس لديك موظفون تابعون للإشراف الميداني أو الإدارة المباشرة'
            })

        sub_ids = subordinates.ids
        Leave = request.env['hr.leave'].sudo()

        pending_leaves = Leave.search([
            ('employee_id', 'in', sub_ids),
            ('state', '=', 'confirm')
        ], order='date_from desc')

        history_leaves = Leave.search([
            ('employee_id', 'in', sub_ids),
            ('state', 'in', ['validate', 'refuse'])
        ], order='date_from desc', limit=20)

        values = self._prepare_portal_layout_values()
        values.update({
            'employee': emp,
            'pending_leaves': pending_leaves,
            'history_leaves': history_leaves,
            'pending_count': len(pending_leaves),
            'page_name': 'team_leaves',
            'success': success,
            'error': error,
            'format_tz': format_tz_dynamic,
            'default_url': '/my/team/leaves',
        })
        return request.render('jandalah_employee_portal.portal_team_leaves_view', values)

    @http.route('/my/team/leaves/action', type='http', auth='user', methods=['POST'], website=True, csrf=True)
    def portal_team_leave_action(self, leave_id=None, action=None, **kw):
        """Executes Approve or Refuse on subordinate leave requests."""
        emp = self._get_employee()
        if not emp or not leave_id or action not in ['approve', 'refuse']:
            return request.redirect('/my/team/leaves?error=invalid_request')

        subordinates = self._get_subordinates(emp)
        sub_ids = subordinates.ids

        leave = request.env['hr.leave'].sudo().browse(int(leave_id))
        if not leave.exists() or leave.employee_id.id not in sub_ids:
            return request.redirect('/my/team/leaves?error=unauthorized')

        try:
            if action == 'approve':
                if hasattr(leave, 'action_approve'):
                    leave.sudo().action_approve()
                elif hasattr(leave, 'action_validate'):
                    leave.sudo().action_validate()
                return request.redirect('/my/team/leaves?success=approved')
            elif action == 'refuse':
                if hasattr(leave, 'action_refuse'):
                    leave.sudo().action_refuse()
                return request.redirect('/my/team/leaves?success=refused')
        except Exception as e:
            _logger.error("Error executing supervisor leave action: %s", e)
            return request.redirect(f'/my/team/leaves?error={str(e)}')
