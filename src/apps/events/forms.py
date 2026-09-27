from datetime import UTC, datetime
from typing import Any

from django import forms

from apps.events.models import QuestionKind, VotingMode, VotingScheme, Weighting


class UtcDateTimeField(forms.DateTimeField):
    """A datetime-local input interpreted as UTC. All event times are shown in UTC."""

    widget = forms.DateTimeInput(attrs={"type": "datetime-local"}, format="%Y-%m-%dT%H:%M")

    def __init__(self, **kwargs: Any) -> None:
        kwargs.setdefault("required", False)
        kwargs.setdefault("input_formats", ["%Y-%m-%dT%H:%M", "%Y-%m-%dT%H:%M:%S"])
        super().__init__(**kwargs)

    def to_python(self, value: Any) -> datetime | None:
        result = super().to_python(value)
        if result is not None and result.tzinfo is None:
            result = result.replace(tzinfo=UTC)
        return result


class EventCreateForm(forms.Form):
    name = forms.CharField(max_length=200)
    template = forms.ChoiceField(required=False)
    submissions_close_at = UtcDateTimeField(label="Submissions close (UTC)")

    def __init__(self, *args: Any, templates: list[tuple[str, str]], **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.fields["template"].choices = [("", "Start empty"), *templates]


class EventSettingsForm(forms.Form):
    name = forms.CharField(max_length=200)
    description = forms.CharField(widget=forms.Textarea(attrs={"rows": 3}), required=False)
    registration_opens_at = UtcDateTimeField(label="Registration opens (UTC)")
    registration_closes_at = UtcDateTimeField(label="Registration closes (UTC)")
    submissions_open_at = UtcDateTimeField(label="Submissions open (UTC)")
    submissions_close_at = UtcDateTimeField(label="Submissions close (UTC)")
    judging_closes_at = UtcDateTimeField(label="Judging closes (UTC)")
    reviews_per_project = forms.IntegerField(min_value=1, max_value=20)
    max_load_per_judge = forms.IntegerField(min_value=1, max_value=200)
    max_team_size = forms.IntegerField(min_value=1, max_value=20)
    blind_judging = forms.BooleanField(required=False, label="Blind judging (hide team identities)")
    allow_multiple_projects = forms.BooleanField(required=False)
    voting_mode = forms.ChoiceField(choices=VotingMode.choices)
    voting_scheme = forms.ChoiceField(choices=VotingScheme.choices)
    qv_credits = forms.IntegerField(min_value=1, max_value=100, label="Quadratic voting credits")
    voting_opens_at = UtcDateTimeField(label="Voting opens (UTC)")
    voting_closes_at = UtcDateTimeField(label="Voting closes (UTC)")


class CriterionForm(forms.Form):
    key = forms.SlugField(max_length=60, required=False)
    label = forms.CharField(max_length=160, required=False)
    weight = forms.DecimalField(max_digits=7, decimal_places=3, required=False)
    min_score = forms.IntegerField(required=False, initial=1)
    max_score = forms.IntegerField(required=False, initial=5)
    is_gate = forms.BooleanField(required=False)
    gate_threshold = forms.DecimalField(max_digits=7, decimal_places=3, required=False)
    is_bonus = forms.BooleanField(required=False)
    description = forms.CharField(required=False, max_length=500)


CriterionFormSet = forms.formset_factory(CriterionForm, extra=2)


class RubricMetaForm(forms.Form):
    weighting = forms.ChoiceField(choices=Weighting.choices)


class TemplateForm(forms.Form):
    key = forms.ChoiceField()

    def __init__(self, *args: Any, templates: list[tuple[str, str]], **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.fields["key"].choices = templates


class TrackForm(forms.Form):
    name = forms.CharField(max_length=120)
    description = forms.CharField(max_length=500, required=False)


class PrizeForm(forms.Form):
    name = forms.CharField(max_length=120)
    rank = forms.IntegerField(min_value=1)
    amount = forms.CharField(max_length=60, required=False)


class QuestionForm(forms.Form):
    label = forms.CharField(max_length=200)
    kind = forms.ChoiceField(choices=QuestionKind.choices)
    choices = forms.CharField(
        required=False, help_text="For choice questions: one option per line."
    )
    required = forms.BooleanField(required=False)


class GrantForm(forms.Form):
    email = forms.EmailField()
    name = forms.CharField(max_length=200, required=False)
    role = forms.ChoiceField(
        choices=[("judge", "Judge"), ("organizer", "Organizer"), ("participant", "Participant")]
    )


class PhaseForm(forms.Form):
    to = forms.CharField(max_length=20)
    reason = forms.CharField(required=False, widget=forms.Textarea(attrs={"rows": 2}))
    override = forms.BooleanField(required=False)
