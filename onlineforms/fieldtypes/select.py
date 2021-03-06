from django.forms import widgets
from onlineforms.fieldtypes.base import FieldBase, FieldConfigForm
from django import forms
from django.utils.safestring import mark_safe
from django.utils.html import conditional_escape as escape
import re

class RadioSelectField(FieldBase):
    choices = True

    class RadioSelectConfigForm(FieldConfigForm):
        def __init__(self, config=None):
            super(self.__class__, self).__init__(config)
            
            self.config = config

            if self.config:
                keys = [c for c in self.config if c.startswith("choice_") and self.config[c]]
                keys = sorted(keys, key=lambda choice: (int) (re.findall(r'\d+', choice)[0]))
            else:
                keys = []

            for k in keys:
                self.fields[k] = forms.CharField(required=False, label="Choice")
        
    def make_config_form(self):
        return self.RadioSelectConfigForm(self.config)

    def make_entry_field(self, fieldsubmission=None):
        the_choices = [(k, v) for k, v in self.config.iteritems() if k.startswith("choice_") and self.config[k]]
        the_choices = sorted(the_choices, key=lambda choice: (int) (re.findall(r'\d+', choice[0])[0]))

        c = forms.ChoiceField(required=self.config['required'],
            label=self.config['label'],
            help_text=self.config['help_text'],
            choices=the_choices,
            widget=forms.RadioSelect())

        if fieldsubmission:
            initial=fieldsubmission.data['info']
            c.initial=initial

        return c

    def serialize_field(self, cleaned_data):
        return {'info': cleaned_data}

    def to_html(self, fieldsubmission=None):
        return mark_safe('<p>' + self.to_text(fieldsubmission) + '</p>')

    def to_text(self, fieldsubmission):
        choice = fieldsubmission.data['info']
        if choice in self.config:
            return self.config[choice]
        else:
            return 'None selected'


class DropdownSelectField(FieldBase):
    choices = True

    class DropdownSelectConfigForm(FieldConfigForm):
        def __init__(self, config=None):
            super(self.__class__, self).__init__(config)

            self.config = config

            if self.config:
                keys = [c for c in self.config if c.startswith("choice_") and self.config[c]]
                keys = sorted(keys, key=lambda choice: (int) (re.findall(r'\d+', choice)[0]))
            else:
                keys = []

            for k in keys:
                self.fields[k] = forms.CharField(required=False, label="Choice")


    def make_config_form(self):
        config = self.DropdownSelectConfigForm(self.config)
        return config

    def make_entry_field(self, fieldsubmission=None):
        the_choices = [(k, v) for k, v in self.config.iteritems() if k.startswith("choice_") and self.config[k]]
        the_choices = sorted(the_choices, key=lambda choice: (int) (re.findall(r'\d+', choice[0])[0]))

        c = forms.ChoiceField(required=self.config['required'],
            label=self.config['label'],
            help_text=self.config['help_text'],
            choices=the_choices)

        if fieldsubmission:
            initial=fieldsubmission.data['info']
            c.initial=initial


        if not self.config['required']:
            c.choices.insert(0, ('', u'\u2014'))

        return c

    def serialize_field(self, cleaned_data):
        return {'info': cleaned_data}

    def to_html(self, fieldsubmission=None):
        return mark_safe('<p>' + escape(self.to_text(fieldsubmission)) + '</p>')

    def to_text(self, fieldsubmission=None):
        choice = fieldsubmission.data['info']
        if choice in self.config:
            return self.config[choice]
        else:
            return 'None selected'


class MultipleSelectField(FieldBase):
    choices = True

    class MultipleSelectConfigForm(FieldConfigForm):
        def __init__(self, config=None):
            super(self.__class__, self).__init__(config)

            self.config = config

            if self.config:
                keys = [c for c in self.config if c.startswith("choice_") and self.config[c]]
                keys = sorted(keys, key=lambda choice: (int) (re.findall(r'\d+', choice)[0]))
            else:
                keys = []                

            for k in keys:
                self.fields[k] = forms.CharField(required=False, label="Choice")

    def make_config_form(self):
        return self.MultipleSelectConfigForm(self.config)

    def make_entry_field(self, fieldsubmission=None):

        the_choices = [(k, v) for k, v in self.config.iteritems() if k.startswith("choice_") and self.config[k]]
        the_choices = sorted(the_choices, key=lambda choice: (int) (re.findall(r'\d+', choice[0])[0]))

        initial = []

        if fieldsubmission:
            initial=fieldsubmission.data['info']

        c = self.CustomMultipleChoiceField(required=self.config['required'],
            label=self.config['label'],
            help_text=self.config['help_text'],
            choices=the_choices,
            widget=forms.CheckboxSelectMultiple(),
            initial=initial)

        return c

    class CustomMultipleChoiceField(forms.MultipleChoiceField):
        def clean(self, data=None):
            return data

    def serialize_field(self, cleaned_data):
        return {'info': cleaned_data}

    def to_html(self, fieldsubmission=None):

        the_choices = [(k, v) for k, v in self.config.iteritems() if k.startswith("choice_") and self.config[k]]
        the_choices = sorted(the_choices, key=lambda choice: (int) (re.findall(r'\d+', choice[0])[0]))

        initial = []

        if fieldsubmission:
            initial = fieldsubmission.data['info']

        display_values = [dict(the_choices)[str(i)] for i in initial]

        if display_values:
            output = '<ul>'

            for item in display_values:
                output += '<li>%s</li>' % escape(str(item))
            output += '</ul>'
        else:
            output = '<p class="empty">None selected</p>'

        return mark_safe(output)

    def to_text(self, fieldsubmission=None):

        the_choices = [(k, v) for k, v in self.config.iteritems() if k.startswith("choice_") and self.config[k]]
        the_choices = sorted(the_choices, key=lambda choice: (int) (re.findall(r'\d+', choice[0])[0]))

        initial = []

        if fieldsubmission:
            initial = fieldsubmission.data['info']

        display_values = [dict(the_choices)[str(i)] for i in initial]
        return ';'.join(display_values)
