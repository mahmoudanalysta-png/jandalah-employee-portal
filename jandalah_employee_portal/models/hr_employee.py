# -*- coding: utf-8 -*-
import math
from odoo import models, fields, api

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
