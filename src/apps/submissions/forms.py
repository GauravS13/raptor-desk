from typing import Any

from django import forms

from apps.events.models import CustomQuestion, Event


class SubmissionForm(forms.Form):
    name = forms.CharField(max_length=200, label="Project name")
    tagline = forms.CharField(max_length=300, required=False, label="One-line tagline")
    description = forms.CharField(widget=forms.Textarea(attrs={"rows": 6}), required=False)
    repo_url = forms.URLField(
        max_length=500, required=False, assume_scheme="https", label="Repository URL"
    )
    video_url = forms.URLField(
        max_length=500, required=False, assume_scheme="https", label="Demo video URL"
    )
    live_url = forms.URLField(
        max_length=500, required=False, assume_scheme="https", label="Live link"
    )
    tech_tags = forms.CharField(
        max_length=300, required=False, label="Tech tags", help_text="Comma separated."
    )
    track = forms.ChoiceField(required=False)

    def __init__(self, *args: Any, event: Event, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.event = event
        tracks = [(t.pk, t.name) for t in event.tracks.all()]
        if tracks:
            self.fields["track"].choices = [("", "Choose a track"), *tracks]
        else:
            del self.fields["track"]
        for question in event.questions.all():
            self.fields[f"q_{question.pk}"] = self._question_field(question)

    @staticmethod
    def _question_field(question: CustomQuestion) -> forms.Field:
        common = {"label": question.label, "required": False, "help_text": question.help_text}
        if question.kind == "long_text":
            return forms.CharField(widget=forms.Textarea(attrs={"rows": 3}), **common)
        if question.kind == "url":
            return forms.URLField(assume_scheme="https", **common)
        if question.kind == "choice":
            choices = [("", "Choose"), *[(c, c) for c in question.choices]]
            return forms.ChoiceField(choices=choices, **common)
        if question.kind == "bool":
            return forms.ChoiceField(
                choices=[("", "Choose"), ("yes", "Yes"), ("no", "No")], **common
            )
        return forms.CharField(max_length=500, **common)

    def answers(self) -> dict[str, str]:
        return {
            name.removeprefix("q_"): str(value or "")
            for name, value in self.cleaned_data.items()
            if name.startswith("q_")
        }

    def tags(self) -> list[str]:
        raw = self.cleaned_data.get("tech_tags") or ""
        return [tag.strip() for tag in raw.split(",") if tag.strip()]
