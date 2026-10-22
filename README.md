# Jandalah Employee Self-Service Portal & Mobile App
## بوابة الخدمة الذاتية لموظفي مجموعة جندله وتطبيق الموبايل (Odoo 16 / PWA / Android & iOS)

---

### نظرة عامة | Overview
مشروع متكامل لبوابة الخدمة الذاتية وتطبيق الموبايل لموظفي مجموعة جندله والشركات السبع التابعة لها، يوفر:
1. **تسجيل الحضور والانصراف الذكي:** التحقق الجغرافي الدقيق عبر الأقمار الصناعية (GPS) مع منع التلاعب وسجلات البصمة اللحظية.
2. **طلبات الإجازات والاستئذانات:** تقديم ومتابعة رصيد الإجازات ومسارات الموافقات الإدارية.
3. **تطبيق الويب التقدمي (PWA):** أيقونات الشاشة الرئيسية وتثبيت التطبيق مباشرة.
4. **تطبيق الموبايل (Android & iOS):** بنية مهيأة عبر تقنية Capacitor لتحويل البوابة إلى تطبيق أصيل يُرفع على متجري Google Play و Apple App Store.

---

### هيكل المستودع | Repository Structure

```text
jandalah-employee-portal/
├── jandalah_employee_portal/       # موديول أودو 16 الأساسي (Odoo 16 Backend & Portal Module)
│   ├── controllers/                # مسارات ومتحكمات البوابة ونقاط المزامنة (Portal Endpoints)
│   │   ├── __init__.py
│   │   └── portal.py
│   ├── models/                     # نماذج البيانات وتوسعة الموظفين (HR Models)
│   │   ├── __init__.py
│   │   └── hr_employee.py
│   ├── security/                   # صلاحيات الوصول وقواعد الأمان (Access Rights)
│   │   └── ir.model.access.csv
│   ├── static/                     # الواجهات البرمجية والأيقونات (PWA Assets & Icons)
│   │   └── src/
│   │       ├── img/                # أيقونات التطبيق الرسمية بمقاسات 192 و 512
│   │       └── js/
│   ├── views/                      # شاشات وقوالب الويب (Mobile-First XML Views)
│   │   └── portal_templates.xml
│   ├── __init__.py
│   └── __manifest__.py
├── mobile/                         # إعدادات ومشروع الموبايل (Capacitor Mobile App Setup)
│   ├── capacitor.config.json       # ملف تهيئة التطبيق ومعرف الحزمة ورابط السيرفر
│   ├── package.json                # حزم Capacitor والمكتبات الأصلية
│   ├── www/                        # شاشة البدء والأيقونات
│   └── README.md                   # دليل المطورين لبناء تطبيقات Android و iOS
├── .gitignore
└── README.md
```

---

### أولاً: موديول أودو (Odoo 16 Module)

#### المتطلبات والاعتماديات:
- `Odoo 16.0` (Community أو Enterprise)
- الموديلات التابعة:
  - `portal`
  - `hr`
  - `hr_attendance`
  - `hr_holidays`

#### التثبيت في سيرفر أودو:
1. انسخ مجلد `jandalah_employee_portal` إلى مسار الـ `addons` في سيرفر أودو.
2. حدّث قائمة التطبيقات في أودو:
   ```bash
   odoo-bin -u jandalah_employee_portal -d <database_name>
   ```
3. ادخل كمسؤول وقم بتثبيت الموديول: **Jandalah Employee Self-Service Portal**.

#### المسارات البرمجية المتاحة (API & Endpoints):
- `/my` : لوحة تحكم الموظف الرئيسية.
- `/my/attendance` : شاشة تسجيل الحضور والانصراف بالـ GPS وسجل الحركات.
- `/my/leaves` : شاشة تقديم الإجازات ومتابعة الحالة.
- `/jandalah/manifest.json` : ملف تعريف الـ PWA.
- `/jandalah/sw.js` : مشغل الخدمة (Service Worker) للتخزين المؤقت.

---

### ثانياً: بناء تطبيقات الموبايل (Android & iOS)

تم تجهيز مجلد `mobile` بحزمة **Capacitor 6** لتمكين المطورين من بناء حزم المتاجر الرسمية:

#### خطوات توليد حزمة أندرويد لمتجر Google Play:
```bash
cd mobile
npm install
npx cap add android
npx cap sync
npx cap open android
```
- من داخل **Android Studio**:
  1. Build -> Generate Signed Bundle / APK.
  2. اختر **Android App Bundle (.aab)**.
  3. وقع الحزمة بملف المفاتيح الخاص بمجموعة جندله: `keystore`.
  4. ارفع ملف `.aab` مباشرة إلى **Google Play Console**.

#### خطوات توليد حزمة آيفون لمتجر Apple App Store:
*(تشترط شركة آبل جهاز Mac مثبت عليه برنامج Xcode)*
```bash
cd mobile
npm install
npx cap add ios
npx cap sync
npx cap open ios
```
- من داخل **Xcode**:
  1. اضبط الحساب وفريق التطوير في خانة **Signing & Capabilities**.
  2. تأكد من ضبط معرف التطبيق: `sa.com.jandalah.portal`.
  3. اختر Product -> Archive.
  4. اضغط Distribute App لرفع الحزمة مباشرة إلى **App Store Connect**.

---

### ثالثاً: المزامنة السحابية (Enterprise Sync Architecture)
يحتوي المتحكم `portal.py` على دالة مزامنة تلقائية عبر XML-RPC:
`sync_attendance_to_enterprise`
تقوم بنقل بصمات الحضور والانصراف مع إحداثيات الـ GPS ونوع المتصفح إلى قاعدة البيانات المركزية فور تسجيل الموظف.

---

### الترخيص والملكية
كافة الحقوق محفوظة لـ **مجموعة جندله القابضة** و **شركة أكوادك للحلول البرمجية (AQuadic)** 2026.
