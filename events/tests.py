from rest_framework.test import APITestCase

from events.models import Event, Reservation


class EventHubAPITests(APITestCase):
    def setUp(self):
        self.event = Event.objects.create(
            title='PyCon India 2025',
            venue='NIMHANS Convention Centre, Bangalore',
            date='2025-09-20',
            total_seats=500,
            available_seats=500,
            status='upcoming',
        )

    def test_create_and_list_events(self):
        response = self.client.post('/api/events/', {
            'title': 'DjangoCon',
            'venue': 'Hyderabad',
            'date': '2025-10-01',
            'total_seats': 100,
            'available_seats': 100,
            'status': 'upcoming',
        }, format='json')
        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.data['reservations_count'], 0)

        listing = self.client.get('/api/events/')
        self.assertEqual(listing.status_code, 200)
        self.assertEqual(len(listing.data), 2)

    def test_event_seat_validation(self):
        response = self.client.post('/api/events/', {
            'title': 'Too many seats',
            'venue': 'Hall',
            'date': '2025-11-01',
            'total_seats': 10,
            'available_seats': 11,
            'status': 'upcoming',
        }, format='json')
        self.assertEqual(response.status_code, 400)
        self.assertIn(
            'available_seats cannot exceed total_seats.',
            response.data['non_field_errors'],
        )

    def test_filter_events_by_status_and_venue(self):
        Event.objects.create(
            title='Past meetup',
            venue='Town Hall, Pune',
            date='2024-01-01',
            total_seats=20,
            available_seats=20,
            status='completed',
        )
        by_status = self.client.get('/api/events/', {'status': 'completed'})
        self.assertEqual(by_status.status_code, 200)
        self.assertEqual(len(by_status.data), 1)
        self.assertEqual(by_status.data[0]['title'], 'Past meetup')

        by_venue = self.client.get('/api/events/', {'venue': 'bangalore'})
        self.assertEqual(len(by_venue.data), 1)
        self.assertEqual(by_venue.data[0]['id'], self.event.id)

    def test_reservation_deducts_seats_and_counts_confirmed_only(self):
        response = self.client.post('/api/reservations/', {
            'event': self.event.id,
            'attendee_name': 'Priya Sharma',
            'attendee_email': 'priya@example.com',
            'seats_reserved': 2,
        }, format='json')
        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.data['status'], 'confirmed')
        self.assertEqual(response.data['attendee_name'], 'Priya Sharma')

        self.event.refresh_from_db()
        self.assertEqual(self.event.available_seats, 498)

        detail = self.client.get(f'/api/events/{self.event.id}/')
        self.assertEqual(detail.data['reservations_count'], 1)

    def test_overbooking_returns_400(self):
        self.event.available_seats = 1
        self.event.save(update_fields=['available_seats'])
        response = self.client.post('/api/reservations/', {
            'event': self.event.id,
            'attendee_name': 'Amit',
            'attendee_email': 'amit@example.com',
            'seats_reserved': 2,
        }, format='json')
        self.assertEqual(response.status_code, 400)
        self.assertEqual(
            response.data['non_field_errors'],
            ['Only 1 seat(s) available.'],
        )
        self.assertEqual(Reservation.objects.count(), 0)
        self.event.refresh_from_db()
        self.assertEqual(self.event.available_seats, 1)

    def test_cannot_reserve_for_completed_event(self):
        self.event.status = 'completed'
        self.event.save(update_fields=['status'])
        response = self.client.post('/api/reservations/', {
            'event': self.event.id,
            'attendee_name': 'Amit',
            'attendee_email': 'amit@example.com',
            'seats_reserved': 1,
        }, format='json')
        self.assertEqual(response.status_code, 400)
        self.assertIn(
            'Cannot reserve seats for a completed event.',
            response.data['non_field_errors'],
        )

    def test_cancel_restores_seats_and_second_cancel_fails(self):
        created = self.client.post('/api/reservations/', {
            'event': self.event.id,
            'attendee_name': 'Priya Sharma',
            'attendee_email': 'priya@example.com',
            'seats_reserved': 2,
        }, format='json')
        reservation_id = created.data['id']

        cancelled = self.client.post(f'/api/reservations/{reservation_id}/cancel/')
        self.assertEqual(cancelled.status_code, 200)
        self.assertEqual(cancelled.data['status'], 'cancelled')

        self.event.refresh_from_db()
        self.assertEqual(self.event.available_seats, 500)

        detail = self.client.get(f'/api/events/{self.event.id}/')
        self.assertEqual(detail.data['reservations_count'], 0)

        again = self.client.post(f'/api/reservations/{reservation_id}/cancel/')
        self.assertEqual(again.status_code, 400)
        self.assertEqual(again.data, {'error': 'Already cancelled.'})
        self.event.refresh_from_db()
        self.assertEqual(self.event.available_seats, 500)

    def test_filter_reservations_by_event_id(self):
        other = Event.objects.create(
            title='Other',
            venue='Delhi',
            date='2025-12-01',
            total_seats=10,
            available_seats=10,
        )
        self.client.post('/api/reservations/', {
            'event': self.event.id,
            'attendee_name': 'Priya Sharma',
            'attendee_email': 'priya@example.com',
            'seats_reserved': 1,
        }, format='json')
        self.client.post('/api/reservations/', {
            'event': other.id,
            'attendee_name': 'Rahul',
            'attendee_email': 'rahul@example.com',
            'seats_reserved': 1,
        }, format='json')

        filtered = self.client.get('/api/reservations/', {'event_id': self.event.id})
        self.assertEqual(filtered.status_code, 200)
        self.assertEqual(len(filtered.data), 1)
        self.assertEqual(filtered.data[0]['event'], self.event.id)

    def test_delete_restores_confirmed_seats(self):
        created = self.client.post('/api/reservations/', {
            'event': self.event.id,
            'attendee_name': 'Priya Sharma',
            'attendee_email': 'priya@example.com',
            'seats_reserved': 4,
        }, format='json')
        response = self.client.delete(f"/api/reservations/{created.data['id']}/")
        self.assertEqual(response.status_code, 204)
        self.event.refresh_from_db()
        self.assertEqual(self.event.available_seats, 500)

    def test_updating_seats_adjusts_availability(self):
        created = self.client.post('/api/reservations/', {
            'event': self.event.id,
            'attendee_name': 'Priya Sharma',
            'attendee_email': 'priya@example.com',
            'seats_reserved': 2,
        }, format='json')
        updated = self.client.patch(
            f"/api/reservations/{created.data['id']}/",
            {'seats_reserved': 5},
            format='json',
        )
        self.assertEqual(updated.status_code, 200)
        self.assertEqual(updated.data['seats_reserved'], 5)
        self.event.refresh_from_db()
        self.assertEqual(self.event.available_seats, 495)

        rejected = self.client.patch(
            f"/api/reservations/{created.data['id']}/",
            {'seats_reserved': 501},
            format='json',
        )
        self.assertEqual(rejected.status_code, 400)
        self.event.refresh_from_db()
        self.assertEqual(self.event.available_seats, 495)

    def test_partial_event_update_keeps_seat_check(self):
        response = self.client.patch(
            f'/api/events/{self.event.id}/',
            {'title': 'PyCon India 2025 — Updated'},
            format='json',
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data['available_seats'], 500)
