# django-dirtyfields 0.3, with pull request #10 applied, so .config field changes are detected.
# In turn adapted from http://stackoverflow.com/questions/110803/dirty-fields-in-django

from django.db.models.signals import post_save
import copy


class DirtyFieldsMixin(object):
    def __init__(self, *args, **kwargs):
        super(DirtyFieldsMixin, self).__init__(*args, **kwargs)
        post_save.connect(reset_state,
                          sender=self.__class__,
                          dispatch_uid='{}-DirtyFieldsMixin-sweeper'.format(
                              self.__class__.__name__))
        reset_state(sender=self.__class__, instance=self)

    def _as_dict(self, do_copy=False):
        if do_copy:
            getter = lambda value: copy.copy(value)
        else:
            getter = lambda value: value

        return dict([(f.name, getter(getattr(self, f.name)))
                     for f in self._meta.local_fields
                     if not f.rel])

    def get_dirty_fields(self):
        new_state = self._as_dict()

        return dict([(key, value)
                     for key, value in self._original_state.items()
                     if value != new_state[key]])

    def is_dirty(self):
        # in order to be dirty we need to have been saved at least once, so we
        # check for a primary key and we need our dirty fields to not be empty
        if not self.pk:
            return True
        return {} != self.get_dirty_fields()


def reset_state(sender, instance, **kwargs):
    instance._original_state = instance._as_dict(True)



# local code here...


class ConditionalSaveMixin(DirtyFieldsMixin):
    def save_if_dirty(self, *args, **kwargs):
        if self.is_dirty():
            self.save(*args, **kwargs)