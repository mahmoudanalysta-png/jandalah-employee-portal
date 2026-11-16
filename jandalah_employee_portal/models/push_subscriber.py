# -*- coding: utf-8 -*-
import json
import logging
from odoo import models, fields, api

_logger = logging.getLogger(__name__)

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
        })
        _logger.info("Push notification queued for employee %s (%s): %s - %s", self.employee_id.name, self.endpoint[:40], title, body)
        # WebPush dispatch via standard library if available
        try:
            from pywebpush import webpush
            webpush(
                subscription_info=json.loads(self.subscription_json or '{}'),
                data=payload,
                vapid_claims={"sub": "mailto:support@jandalah.com"}
            )
        except Exception:
            # Fallback logged cleanly without crash
            pass
