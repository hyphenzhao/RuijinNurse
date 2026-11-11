"""患者管理 CRUD 视图与模型测试（纯 DB，不联网）。"""
import json
from datetime import date

from django.contrib.auth.models import User
from django.test import TestCase

from promotions.models import Patient, QARecord, SurgeryPlan


class PatientCrudTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user('nurse', password='test-pass-123')
        self.client.login(username='nurse', password='test-pass-123')

    def _post_patient(self, form_type, **overrides):
        data = {
            'form_type': form_type,
            'bed_no': '12',
            'name': '张三',
            'gender': 'male',
            'birth_date': '1960-05-01',
            'disease_type': 'parkinson',
            'admission_time': '2026-09-01T09:00',
            'expected_stay_min_days': '10',
            'expected_stay_max_days': '14',
            'cautions': '跌倒风险高',
            'notes': '家属陪护',
            'surgery_plans_json': json.dumps([
                {'planned_time': '2026-09-05T09:00', 'note': 'DBS 植入'},
                {'planned_time': '2026-09-10T09:00', 'note': '复查'},
            ]),
        }
        data.update(overrides)
        return self.client.post('/promotion/patients/', data)

    def test_anonymous_redirects_to_login(self):
        self.client.logout()
        response = self.client.get('/promotion/patients/')
        self.assertEqual(response.status_code, 302)

    def test_patient_create_renders_and_persists(self):
        response = self._post_patient('patient_create')
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, '已创建')
        patient = Patient.objects.get(bed_no='12')
        self.assertEqual(patient.name, '张三')
        self.assertEqual(patient.disease_type, 'parkinson')
        self.assertEqual(patient.expected_stay_min_days, 10)
        self.assertEqual(patient.expected_stay_max_days, 14)
        self.assertEqual(patient.surgery_plans.count(), 2)
        self.assertEqual(patient.scales, {})
        self.assertTrue(patient.is_active)

    def test_patient_create_requires_bed_and_name(self):
        response = self._post_patient('patient_create', bed_no='', name='')
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, '床号和姓名为必填项')
        self.assertEqual(Patient.objects.count(), 0)

    def test_patient_edit_updates_fields_and_replaces_plans(self):
        patient = Patient.objects.create(bed_no='1', name='李四', disease_type='epilepsy')
        SurgeryPlan.objects.create(patient=patient,
                                   planned_time='2026-09-01T10:00+08:00')
        response = self._post_patient(
            'patient_edit', patient_id=patient.id, bed_no='2', name='李四改',
            surgery_plans_json=json.dumps([
                {'planned_time': '2026-09-06T08:30', 'note': '新术式'},
            ]),
        )
        self.assertEqual(response.status_code, 200)
        patient.refresh_from_db()
        self.assertEqual(patient.bed_no, '2')
        self.assertEqual(patient.name, '李四改')
        self.assertEqual(patient.surgery_plans.count(), 1)
        self.assertEqual(patient.surgery_plans.first().note, '新术式')

    def test_patient_delete_is_soft_and_keeps_qa_association(self):
        patient = Patient.objects.create(bed_no='3', name='王五')
        qa = QARecord.objects.create(user=self.user, patient=patient,
                                     question='术后注意什么', answer='注意休息')
        response = self.client.post('/promotion/patients/', {
            'form_type': 'patient_delete', 'patient_id': patient.id,
        })
        self.assertEqual(response.status_code, 200)
        patient.refresh_from_db()
        self.assertFalse(patient.is_active)
        qa.refresh_from_db()
        self.assertEqual(qa.patient, patient)  # SET_NULL 未触发，关联保留

    def test_patient_restore(self):
        patient = Patient.objects.create(bed_no='4', name='赵六', is_active=False)
        self.client.post('/promotion/patients/', {
            'form_type': 'patient_restore', 'patient_id': patient.id,
        })
        patient.refresh_from_db()
        self.assertTrue(patient.is_active)

    def test_invalid_surgery_plans_json_is_ignored(self):
        response = self._post_patient('patient_create', surgery_plans_json='not-json[[')
        self.assertEqual(response.status_code, 200)
        patient = Patient.objects.get(bed_no='12')
        self.assertEqual(patient.surgery_plans.count(), 0)

    def test_stay_range_min_greater_than_max_rejected(self):
        response = self._post_patient('patient_create',
                                      expected_stay_min_days='20',
                                      expected_stay_max_days='5')
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, '最短天数不能大于最长天数')
        self.assertEqual(Patient.objects.count(), 0)

    def test_age_property(self):
        patient = Patient.objects.create(bed_no='5', name='钱七',
                                         birth_date=date(1960, 5, 1))
        self.assertEqual(patient.age, 66)  # 2026-09-08 时 1960-05-01 出生为 66 周岁
        patient.birth_date = None
        patient.save()
        self.assertIsNone(patient.age)

    def test_list_page_shows_key_columns(self):
        Patient.objects.create(bed_no='6', name='孙八', disease_type='dystonia',
                               expected_stay_min_days=3, expected_stay_max_days=7)
        response = self.client.get('/promotion/patients/')
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, '孙八')
        self.assertContains(response, '肌张力障碍')
        self.assertContains(response, '3 ~ 7 天')
