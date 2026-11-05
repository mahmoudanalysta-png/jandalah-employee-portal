# -*- coding: utf-8 -*-
import logging
from odoo import http, _
from odoo.http import request

_logger = logging.getLogger(__name__)

try:
    from odoo.addons.auth_signup.controllers.main import AuthSignupHome
except ImportError:
    from odoo.addons.web.controllers.home import Home as AuthSignupHome


class JandalahAuthSignupHome(AuthSignupHome):

    @http.route('/web/signup', type='http', auth='public', website=True, sitemap=False)
    def web_auth_signup(self, *args, **kw):
        """Enforces that only pre-registered employees in hr.employee can sign up for portal accounts."""
        if request.httprequest.method == 'POST':
            login = (kw.get('login') or '').strip()
            if not login:
                qcontext = self.get_auth_signup_qcontext()
                qcontext['error'] = 'يرجى إدخال البريد الإلكتروني.'
                return request.render('auth_signup.signup', qcontext)

            # Check if this email matches an existing employee in hr.employee
            emp = request.env['hr.employee'].sudo().search([
                '|',
                ('work_email', '=ilike', login),
                ('private_email', '=ilike', login),
            ], limit=1)

            if not emp:
                qcontext = self.get_auth_signup_qcontext()
                qcontext['error'] = 'عذراً، هذا البريد الإلكتروني غير مسجل في سجلات موظفي شركة جندله. يرجى مراجعة إدارة الموارد البشرية.'
                return request.render('auth_signup.signup', qcontext)

        # Delegate to standard signup
        response = super().web_auth_signup(*args, **kw)

        # Post-signup: auto-link the newly created user to the matched employee record
        if request.httprequest.method == 'POST' and request.session.uid:
            try:
                user = request.env['res.users'].sudo().browse(request.session.uid)
                if user and user.exists():
                    login = (user.login or user.email or '').strip()
                    emp = request.env['hr.employee'].sudo().search([
                        '|',
                        ('work_email', '=ilike', login),
                        ('private_email', '=ilike', login),
                    ], limit=1)
                    if emp:
                        vals = {'tz': 'Asia/Riyadh'}
                        if not emp.user_id:
                            vals['user_id'] = user.id
                        with request.env.cr.savepoint():
                            emp.sudo().write(vals)
                        if not user.tz:
                            with request.env.cr.savepoint():
                                user.sudo().write({'tz': 'Asia/Riyadh'})
            except Exception as e:
                _logger.error("Could not post-link user to employee: %s", e)

        return response
