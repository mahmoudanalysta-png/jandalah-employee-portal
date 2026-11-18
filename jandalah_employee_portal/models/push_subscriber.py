# -*- coding: utf-8 -*-
import json
import logging
import base64
from datetime import datetime
from odoo import models, fields, api

_logger = logging.getLogger(__name__)

# Standard VAPID Keypair for Jandalah PWA Push Notifications (RFC 8292)
VAPID_PUBLIC_KEY = "BDEmktt-z8gbD_wFgEfK_1EZspp_W04tbpR7dtkT9Z1TVYZ6t71Q7F47LGYN8OOFhVnICBjLPYyvarRPfzg2ZiE"
VAPID_PRIVATE_PEM = b"""-----BEGIN PRIVATE KEY-----
MIGHAgEAMBMGByqGSM49AgEGCCqGSM49AwEHBG0wawIBAQQgrmtKa9XWC678x/q4
aD9gTLwKrQI22MFwqjDv4KnyqsahRANCAAQxJpLbfs/IGw/8BYBHyv9RGbKaf1tO
LW6Ue3bZE/WdU1WGere9UOxeOyxmDfDjhYVZyAgYyz2Mr2q0T384NmYh
-----END PRIVATE KEY-----"""

class JandalahPushSubscriber(models.Model):
    _name = 'jandalah.push.subscriber'
    _description = 'مشتركو إشعارات الهاتف المنبثقة'
    _order = 'last_seen desc, id desc'

    employee_id = fields.Many2one('hr.employee', string='الموظف', required=True, ondelete='cascade', index=True)
    user_id = fields.Many2one('res.users', string='المستخدم', index=True)
    endpoint = fields.Char(string='رابط الإشعار (Endpoint)', required=True, index=True)
    p256dh = fields.Char(string='مفتاح التشفير (p256dh)')
    auth = fields.Char(string='مفتاح التوثيق (auth)')
    subscription_json = fields.Text(string='بيانات الاشتراك البرمجية')
    device_name = fields.Char(string='نوع الجهاز / المتصفح', default='هاتف ذكي')
    active = fields.Boolean(string='نشط', default=True)
    last_seen = fields.Datetime(string='آخر ظهور', default=fields.Datetime.now)

    _sql_constraints = [
        ('endpoint_uniq', 'unique(endpoint)', 'رابط اشتراك الإشعارات مسجل مسبقاً.')
    ]

    @api.model
    def register_subscription(self, employee, endpoint, p256dh=None, auth=None, device_name=None, raw_json=None):
        """Registers or updates a mobile device push subscription for an employee."""
        if not employee or not endpoint:
            return False

        sub = self.sudo().search([('endpoint', '=', endpoint)], limit=1)
        vals = {
            'employee_id': employee.id,
            'user_id': self.env.user.id if self.env.user else False,
            'endpoint': endpoint,
            'p256dh': p256dh or '',
            'auth': auth or '',
            'device_name': device_name or 'هاتف ذكي',
            'subscription_json': raw_json if isinstance(raw_json, str) else json.dumps(raw_json or {}),
            'active': True,
            'last_seen': fields.Datetime.now(),
        }
        if sub:
            sub.write(vals)
        else:
            sub = self.sudo().create(vals)
        return sub

    @api.model
    def notify_employee(self, employee, title, body, url='/my'):
        """Triggers a notification to all active devices registered to this employee."""
        if not employee:
            return 0
        subs = self.sudo().search([('employee_id', '=', employee.id), ('active', '=', True)])
        count = 0
        for s in subs:
            try:
                s._send_push(title, body, url)
                count += 1
            except Exception as e:
                _logger.warning("Error pushing notification to sub %s: %s", s.id, e)
        return count

    @api.model
    def notify_all_employees(self, title, body, url='/my'):
        """Broadcasts a notification to all subscribed employees (e.g. for circulars/announcements)."""
        subs = self.sudo().search([('active', '=', True)])
        count = 0
        for s in subs:
            try:
                s._send_push(title, body, url)
                count += 1
            except Exception as e:
                _logger.warning("Error broadcasting notification: %s", e)
        return count

    def _send_push(self, title, body, url='/my'):
        self.ensure_one()
        payload = json.dumps({
            'title': title,
            'body': body,
            'url': url,
            'icon': '/jandalah_employee_portal/static/src/img/icon-192.png',
            'badge': '/jandalah_employee_portal/static/src/img/icon-192.png',
            'tag': f"jandalah-{self.id}-{int(datetime.utcnow().timestamp())}",
        })
        _logger.info("Push notification dispatch for %s (%s): %s", self.employee_id.name, self.endpoint[:40], title)

        try:
            from pywebpush import webpush
            from py_vapid import Vapid

            sub_info = None
            if self.subscription_json:
                try:
                    sub_info = json.loads(self.subscription_json)
                except Exception:
                    sub_info = None

            if not sub_info or not isinstance(sub_info, dict):
                sub_info = {
                    'endpoint': self.endpoint,
                    'keys': {
                        'p256dh': self.p256dh,
                        'auth': self.auth,
                    }
                }

            if not sub_info.get('keys') or not sub_info['keys'].get('p256dh'):
                _logger.warning("Sub %s missing p256dh key, skipping WebPush.", self.id)
                return False

            vapid_obj = Vapid.from_pem(VAPID_PRIVATE_PEM)
            webpush(
                subscription_info=sub_info,
                data=payload,
                vapid_private_key=vapid_obj,
                vapid_claims={"sub": "mailto:support@jandalah.com"}
            )
            _logger.info("WebPush delivered successfully to %s", self.employee_id.name)
            return True
        except Exception as e_push:
            _logger.warning("WebPush dispatch exception for sub %s: %s", self.id, e_push)
            return False

    @api.model
    def _register_hook(self):
        super()._register_hook()
        try:
            with self.env.cr.savepoint():
                cron = self.env.ref('jandalah_employee_portal.ir_cron_overdue_attendance_reminder', raise_if_not_found=False)
                if not cron:
                    model_record = self.env['ir.model'].search([('model', '=', 'jandalah.push.subscriber')], limit=1)
                    if model_record:
                        self.env['ir.cron'].sudo().create({
                            'name': 'جندله | تذكير الموظفين بالانصراف التلقائي (Web Push)',
                            'model_id': model_record.id,
                            'state': 'code',
                            'code': 'model.cron_check_overdue_attendance()',
                            'interval_number': 5,
                            'interval_type': 'minutes',
                            'numbercall': -1,
                            'active': True,
                        })
                else:
                    cron.sudo().write({
                        'interval_number': 5,
                        'interval_type': 'minutes',
                        'active': True,
                    })
        except Exception as e:
            _logger.warning("Error ensuring overdue attendance cron: %s", e)

    @api.model
    def cron_check_overdue_attendance(self):
        """
        Runs every 5 minutes in background to send Web Push reminders to employees
        who have completed their scheduled work shift but haven't checked out yet.
        """
        _logger.info("Executing cron_check_overdue_attendance (every 5 minutes)...")
        Attendance = self.env['hr.attendance'].sudo()
        open_attendances = Attendance.search([
            ('check_out', '=', False),
            ('check_in', '!=', False)
        ])
        if not open_attendances:
            return True

        from datetime import datetime, timedelta, date
        import pytz

        now_utc = datetime.utcnow()
        sent_total = 0

        for att in open_attendances:
            try:
                emp = att.employee_id
                if not emp or not emp.active:
                    continue

                user_tz_name = (emp.user_id and emp.user_id.tz) or (emp.company_id and emp.company_id.partner_id.tz) or 'Africa/Cairo'
                try:
                    user_tz = pytz.timezone(user_tz_name)
                except Exception:
                    user_tz = pytz.timezone('Africa/Cairo')

                now_local = pytz.utc.localize(now_utc).astimezone(user_tz)
                check_in_local = pytz.utc.localize(att.check_in).astimezone(user_tz)
                elapsed_hours = (now_local - check_in_local).total_seconds() / 3600.0

                is_overdue = False
                overdue_msg = ""
                scheduled_end_dt = None

                # 1. Check if punch is from a previous day
                if now_local.date() > check_in_local.date():
                    is_overdue = True
                    overdue_msg = f"مرحباً {emp.name}، تذكير: لديك بصمة حضور مفتوحة من يوم سابق لم يتم تسجيل انصرافها. يرجى تسجيل الانصراف الآن لإغلاق حركتك."
                else:
                    calendar = emp.resource_calendar_id or (emp.company_id and emp.company_id.resource_calendar_id)
                    shift_hours = calendar.hours_per_day if calendar and hasattr(calendar, 'hours_per_day') and calendar.hours_per_day else 8.0

                    if calendar and calendar.attendance_ids:
                        today_dow = str(now_local.weekday())
                        today_atts = calendar.attendance_ids.filtered(lambda a: a.dayofweek == today_dow)
                        if today_atts:
                            max_hour_to = max(today_atts.mapped('hour_to'))
                            h_end = int(max_hour_to)
                            m_end = int(round((max_hour_to - h_end) * 60))
                            scheduled_end_dt = now_local.replace(hour=h_end, minute=m_end, second=0, microsecond=0)

                    if scheduled_end_dt:
                        # 10 minutes grace period after scheduled shift end
                        if now_local >= (scheduled_end_dt + timedelta(minutes=10)):
                            is_overdue = True
                            over_mins = int((now_local - scheduled_end_dt).total_seconds() / 60)
                            end_time_str = scheduled_end_dt.strftime('%I:%M %p').replace('AM', 'ص').replace('PM', 'م')
                            overdue_msg = f"مرحباً {emp.name}، انتهى موعد دوامك الرسمي المقرر (الساعة {end_time_str}) منذ {over_mins} دقيقة. يرجى تسجيل الانصراف الآن!"
                    else:
                        effective_shift = max(8.0, shift_hours)
                        if elapsed_hours >= (effective_shift + 0.5):
                            is_overdue = True
                            over_mins = int((elapsed_hours - effective_shift) * 60)
                            overdue_msg = f"مرحباً {emp.name}، لقد أتممت ساعات العمل المقررة ({int(effective_shift)} ساعات) منذ {over_mins} دقيقة. يرجى تسجيل الانصراف الآن!"

                if not is_overdue:
                    continue

                # Anti-spam logic:
                # 1. Send first notification immediately.
                # 2. Only send follow-up reminder if at least 60 minutes have elapsed since the last reminder.
                should_send = False
                has_sent_field = 'checkout_reminder_sent' in Attendance._fields
                has_time_field = 'last_checkout_reminder_time' in Attendance._fields

                if has_sent_field and not att.checkout_reminder_sent:
                    should_send = True
                elif has_time_field and att.last_checkout_reminder_time:
                    mins_since = (now_utc - att.last_checkout_reminder_time).total_seconds() / 60.0
                    if mins_since >= 60.0:
                        should_send = True
                elif not has_sent_field:
                    should_send = True

                if should_send:
                    _logger.info("Sending overdue checkout Web Push to %s...", emp.name)
                    sent = self.notify_employee(
                        employee=emp,
                        title="تنبيه انتهاء الدوام ⏰",
                        body=overdue_msg,
                        url="/my"
                    )
                    sent_total += sent
                    write_vals = {}
                    if has_sent_field:
                        write_vals['checkout_reminder_sent'] = True
                    if has_time_field:
                        write_vals['last_checkout_reminder_time'] = fields.Datetime.now()
                    if write_vals:
                        att.write(write_vals)
            except Exception as e_emp:
                _logger.warning("Error processing overdue attendance for att %s: %s", att.id, e_emp)

        _logger.info("cron_check_overdue_attendance finished. Total push notifications sent: %s", sent_total)
        return True
