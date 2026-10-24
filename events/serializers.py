from django.db import transaction
from rest_framework import serializers

from .models import Event, Reservation


class EventSerializer(serializers.ModelSerializer):
    reservations_count = serializers.SerializerMethodField()

    class Meta:
        model = Event
        fields = [
            'id', 'title', 'venue', 'date', 'total_seats',
            'available_seats', 'status', 'created_at', 'reservations_count',
        ]

    def get_reservations_count(self, obj):
        return obj.reservations.filter(status='confirmed').count()

    def validate(self, data):
        total_seats = data.get(
            'total_seats',
            getattr(self.instance, 'total_seats', 0),
        )
        available_seats = data.get(
            'available_seats',
            getattr(self.instance, 'available_seats', 0),
        )
        if available_seats > total_seats:
            raise serializers.ValidationError(
                'available_seats cannot exceed total_seats.'
            )
        return data


class ReservationSerializer(serializers.ModelSerializer):
    class Meta:
        model = Reservation
        fields = [
            'id', 'event', 'attendee_name', 'attendee_email',
            'seats_reserved', 'status', 'created_at',
        ]
        read_only_fields = ['status', 'created_at']

    def validate_seats_reserved(self, value):
        if value < 1:
            raise serializers.ValidationError('Must reserve at least 1 seat.')
        return value

    def validate(self, data):
        event = data.get('event')
        if event is None and self.instance is not None:
            event = self.instance.event
        if event is None:
            return data

        if event.status not in ('upcoming', 'ongoing'):
            raise serializers.ValidationError(
                f'Cannot reserve seats for a {event.status} event.'
            )

        seats_reserved = data.get('seats_reserved')
        if seats_reserved is None and self.instance is not None:
            seats_reserved = self.instance.seats_reserved
        if seats_reserved is None:
            return data

        # Seats already held by this reservation are still available to it.
        held = 0
        if (
            self.instance is not None
            and self.instance.status == 'confirmed'
            and self.instance.event_id == event.id
        ):
            held = self.instance.seats_reserved

        if seats_reserved > event.available_seats + held:
            raise serializers.ValidationError(
                f'Only {event.available_seats} seat(s) available.'
            )
        return data

    def create(self, validated_data):
        # Lock the event row so two requests cannot both take the last seats.
        with transaction.atomic():
            event = (
                Event.objects.select_for_update().get(pk=validated_data['event'].pk)
            )
            seats_reserved = validated_data['seats_reserved']
            if event.status not in ('upcoming', 'ongoing'):
                raise serializers.ValidationError(
                    f'Cannot reserve seats for a {event.status} event.'
                )
            if seats_reserved > event.available_seats:
                raise serializers.ValidationError(
                    f'Only {event.available_seats} seat(s) available.'
                )
            event.available_seats -= seats_reserved
            event.save(update_fields=['available_seats'])
            validated_data['event'] = event
            return Reservation.objects.create(**validated_data)

    def update(self, instance, validated_data):
        new_event = validated_data.get('event', instance.event)
        new_seats = validated_data.get('seats_reserved', instance.seats_reserved)

        with transaction.atomic():
            instance = Reservation.objects.select_for_update().get(pk=instance.pk)
            old_event = Event.objects.select_for_update().get(pk=instance.event_id)

            if instance.status == 'confirmed':
                old_event.available_seats += instance.seats_reserved
                old_event.save(update_fields=['available_seats'])

            if new_event.pk == old_event.pk:
                target = old_event
                target.refresh_from_db()
            else:
                target = Event.objects.select_for_update().get(pk=new_event.pk)

            if target.status not in ('upcoming', 'ongoing'):
                raise serializers.ValidationError(
                    f'Cannot reserve seats for a {target.status} event.'
                )

            if instance.status == 'confirmed':
                if new_seats > target.available_seats:
                    raise serializers.ValidationError(
                        f'Only {target.available_seats} seat(s) available.'
                    )
                target.available_seats -= new_seats
                target.save(update_fields=['available_seats'])

            instance.event = target
            instance.attendee_name = validated_data.get(
                'attendee_name', instance.attendee_name
            )
            instance.attendee_email = validated_data.get(
                'attendee_email', instance.attendee_email
            )
            instance.seats_reserved = new_seats
            instance.save()
            return instance
