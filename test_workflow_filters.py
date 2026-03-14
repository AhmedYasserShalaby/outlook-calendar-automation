import unittest
from datetime import datetime

import classifier
import main


class WorkflowFilterTests(unittest.TestCase):
    def test_assignment_reference_uses_assignment_family(self):
        result = classifier.ClassifiedEvent(
            is_actionable=True,
            event_type="assignment",
            course_code="INCS615",
            title="Assignment 1 Submission",
            date="2026-03-19",
        )

        key = main._build_canonical_event_key(
            "Notification System: Assignment 1 – Reference",
            "Please check the attached reference before submission.",
            result,
        )

        self.assertEqual(key, "INCS615|assignment-1|2026-03-19")

    def test_phase_presentation_family_is_stable(self):
        result = classifier.ClassifiedEvent(
            is_actionable=True,
            event_type="presentation",
            course_code="BUAD705",
            title="Phase 1 Presentation – Business Simulation Project",
            date="2026-03-18",
        )

        key = main._build_canonical_event_key(
            "Reminder: Phase 1 Presentation – Business Simulation Project",
            "All groups must be present.",
            result,
        )

        self.assertEqual(key, "BUAD705|phase-1-presentation|2026-03-18")

    def test_noise_email_is_rejected(self):
        result = classifier.ClassifiedEvent(
            is_actionable=True,
            event_type="other",
            course_code="BUAD705",
            title="Hackonomics Workshop",
            date="2026-03-15",
        )

        allowed, reason = main._evaluate_event_eligibility(
            {
                "subject": "Hackonomics Workshop - AI Connects",
                "body": "Register now to reserve your spot in the workshop.",
            },
            result,
            datetime(2026, 3, 14, 6, 23),
        )

        self.assertFalse(allowed)
        self.assertEqual(reason, "noise_marketing_or_registration")

    def test_past_event_is_rejected(self):
        result = classifier.ClassifiedEvent(
            is_actionable=True,
            event_type="lab",
            course_code="BINF602",
            title="Business Intelligence Lab",
            date="2026-03-11",
            time_start="10:15",
        )

        allowed, reason = main._evaluate_event_eligibility(
            {
                "subject": "Business Intelligence Lab",
                "body": "Please bring your laptop to the lab.",
            },
            result,
            datetime(2026, 3, 14, 6, 23),
        )

        self.assertFalse(allowed)
        self.assertEqual(reason, "past_event_date")

    def test_supporting_email_role_is_detected(self):
        result = classifier.ClassifiedEvent(
            is_actionable=True,
            event_type="assignment",
            course_code="INCS615",
            title="Assignment 1 Submission",
            date="2026-03-19",
        )

        role = main._classify_event_role(
            result,
            "Assignment 1 – Reference",
            "Reference material and guidelines for the assignment.",
        )

        self.assertEqual(role, "supporting_email")

    def test_explicit_time_beats_slot_map(self):
        event = classifier.ClassifiedEvent(
            is_actionable=True,
            event_type="quiz",
            course_code="BUAD524",
            title="Quiz 1",
            date="2026-03-15",
            time_start="",
            time_end="",
        )

        classifier._apply_explicit_time_overrides(
            event,
            "Quiz 1 Sunday 15th of March",
            "The quiz will be held in the 3rd slot (11:05 AM) hall C.101.",
        )
        classifier._apply_slot_time_overrides(
            event,
            "Quiz 1 Sunday 15th of March",
            "The quiz will be held in the 3rd slot (11:05 AM) hall C.101.",
        )

        self.assertEqual(event.time_start, "11:05")
        self.assertEqual(event.time_end, "12:35")

    def test_big_data_6bi2_evaluation_moves_to_tuesday(self):
        event = classifier.ClassifiedEvent(
            is_actionable=True,
            event_type="assignment",
            course_code="INCS615",
            title="Big Data Assignment 1 Evaluation",
            date="2026-03-15",
            time_start="08:30",
            time_end="10:00",
        )

        classifier._apply_big_data_evaluation_overrides(
            event,
            "Notification System: Big Data Assignment 1 – Evaluation Schedule [Group: Spring 2026 - INCS 615 - 6BI2 (Practical)]",
            "Sunday labs will have their evaluations on Sunday. Thursday labs will have their evaluations on Tuesday.",
            "2026-03-12",
        )

        self.assertEqual(event.date, "2026-03-17")
        self.assertEqual(event.time_start, "")
        self.assertEqual(event.time_end, "")

    def test_uploaded_phase_update_is_rejected(self):
        result = classifier.ClassifiedEvent(
            is_actionable=True,
            event_type="project",
            course_code="BUAD705",
            title="Business Simulation - Phase 2 Project Work",
            date="2026-03-18",
        )

        allowed, reason = main._evaluate_event_eligibility(
            {
                "subject": "Notification System: Business Simulation - Phase 2 uploaded",
                "body": "Phase 2 has now been added to the project document on the CMS.",
            },
            result,
            datetime(2026, 3, 14, 6, 23),
        )

        self.assertFalse(allowed)
        self.assertEqual(reason, "supporting_material_update")

    def test_next_session_prep_email_is_rejected(self):
        result = classifier.ClassifiedEvent(
            is_actionable=True,
            event_type="lab",
            course_code="INCS617",
            title="Hands-on session on Git & GitHub",
            date="2026-03-15",
        )

        allowed, reason = main._evaluate_event_eligibility(
            {
                "subject": "Notification System: upcoming lab [Group: Spring 2026 - INCS 617 - 6BI2 (Practical)]",
                "body": "Please bring your laptops to the next session and make sure Git is installed on your device.",
            },
            result,
            datetime(2026, 3, 14, 6, 23),
        )

        self.assertFalse(allowed)
        self.assertEqual(reason, "prep_for_future_session_only")


if __name__ == "__main__":
    unittest.main()
