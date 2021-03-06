from django.db import models, transaction
from django.db.models import Count
from autoslug import AutoSlugField
from courselib.slugs import make_slug
from django.conf import settings
import datetime, urlparse, decimal
from django.core.urlresolvers import reverse
from django.core.exceptions import ValidationError
from django.core.cache import cache
from cache_utils.decorators import cached
from courselib.json_fields import JSONField
from courselib.json_fields import getter_setter, config_property
from courselib.conditional_save import ConditionalSaveMixin
from django.utils.safestring import mark_safe
from django.utils.html import escape
from bitfield import BitField
import fractions


def repo_name(offering, slug):
    """
    Label for a SVN repository
    """
    name = offering.subject.upper() + offering.number[0:3] + '-' + offering.semester.name + '-' + slug
    return name

VISA_STATUSES = ( # as taken from SIMS ps_visa_permit_tbl
        ('Perm Resid', 'Permanent Resident'),
        ('Student',    'Student Visa'),          # Student Authorization permitting study in Canada
        ('Diplomat',   'Diplomat'),              # Reciprocal domestic tuition may be extended to dependents of diplomats from certain countries (not all).
        ('Min Permit', "Minister's Permit"),
        ('Other',      'Other Visa'),
        ('Visitor',    "Visitor's Visa"),        # Does not permit long term study in Canada
        ('Unknown',    'Not Known'),
        ('New CDN',    "'New' Canadian citizen"),# Naturalized Canadian citizen whose SFU record previously showed another visa/permit status, such as Permanent Resident.
        ('Conv Refug', 'Convention Refugee'),
        ('Refugee',    'Refugee'),               # Refugee (status granted)
        ('Unknown',    'Non-Canadian, Status Unknown'), # Non-Canadian, Status Unknown (incl refugee claimants)
        ('No Visa St', 'Non-Canadian, No Visa Status'), # Non-Canadian, No Visa Status (student is studying outside Canada)
        ('Live-in Ca', 'Live-in Caregiver'),
        )

class Person(models.Model, ConditionalSaveMixin):
    """
    A person in the system (students, instuctors, etc.).
    """
    emplid = models.PositiveIntegerField(db_index=True, unique=True, null=False,
                                         verbose_name="ID #",
        help_text='Employee ID (i.e. student number)')
    userid = models.CharField(max_length=8, null=True, blank=True, db_index=True, unique=True,
                              verbose_name="User ID",
        help_text='SFU Unix userid (i.e. part of SFU email address before the "@").')
    last_name = models.CharField(max_length=32)
    first_name = models.CharField(max_length=32)
    middle_name = models.CharField(max_length=32, null=True, blank=True)
    pref_first_name = models.CharField(max_length=32, null=True, blank=True)
    title = models.CharField(max_length=4, null=True, blank=True)
    temporary = models.BooleanField(default=False)
    config = JSONField(null=False, blank=False, default={}) # addition configuration stuff
        # 'email': email, if not the default userid@sfu.ca
        # 'pref_first_name': really, truly preferred first name (which can be set in DB if necessary)
        # 'phones': dictionary of phone number values. Possible keys: 'pref', 'home', 'cell', 'main'
        # 'addresses': dictionary of phone number values. Possible keys: 'home', 'mail'
        # 'gender': 'M', 'F', 'U'
        # 'citizen': country of citizenship (e.g. 'Canada')
        # 'visa': Canadian visa status (e.g. 'No visa st', 'Perm resid')
        # 'birthdate': birth date (e.g. '1980-12-31')
        # 'applic_email': application email address
        # 'gpa': Most recent CGPA for this student
        # 'ccredits': Number of completed credits
        # 'sin': Social Insurance Number (usually populated by TA/RA contracts)
        # 'nonstudent_hs': highschool field from NonStudent record
        # 'nonstudent_colg': college field from NonStudent record
        # 'nonstudent_notes': notes field from NonStudent record
        # 'phone_ext': local phone number (for faculty/staff) (e.g. '25555')
        # 'form_email': email address to be used by the onlineforms app for this person
        # 'external_email': external email for non-SFU grad committee members

    defaults = {'email': None, 'gender': 'U', 'addresses': {}, 'gpa': 0.0, 'ccredits': 0.0, 'visa': None,
                'citizen': None, 'nonstudent_hs': '',  'nonstudent_colg': '', 'nonstudent_notes': None,
                'sin': '000000000', 'phone_ext': None}
    _, set_email = getter_setter('email')
    gender, _ = getter_setter('gender')
    addresses, _ = getter_setter('addresses')
    gpa, _ = getter_setter('gpa')
    ccredits, _ = getter_setter('ccredits')
    # see VISA_STATUSES above for list of possibilities
    visa, _ = getter_setter('visa')
    citizen, _ = getter_setter('citizen')
    sin, set_sin = getter_setter('sin')
    phone_ext, set_phone_ext = getter_setter('phone_ext')
    nonstudent_hs, set_nonstudent_hs = getter_setter('nonstudent_hs')
    nonstudent_colg, set_nonstudent_colg = getter_setter('nonstudent_colg')
    nonstudent_notes, set_nonstudent_notes = getter_setter('nonstudent_notes')
    _, set_title = getter_setter('title')


    @staticmethod
    def emplid_header():
        return "ID Number"
    @staticmethod
    def userid_header():
        return "Userid"

    def __unicode__(self):
        return "%s, %s" % (self.last_name, self.first_name)
    def name(self):
        return "%s %s" % (self.first_name, self.last_name)
    def sortname(self):
        return "%s, %s" % (self.last_name, self.first_name)
    def initials(self):
        return "%s%s" % (self.first_name[0], self.last_name[0])
    def full_email(self):
        return "%s <%s>" % (self.name(), self.email())
    def real_pref_first(self):
        return self.config.get('pref_first_name', None) or self.pref_first_name or self.first_name
    def name_pref(self):
        return "%s %s" % (self.real_pref_first(), self.last_name)
    def first_with_pref(self):
        name = self.first_name
        pref = self.real_pref_first()
        if pref != self.first_name:
            name += ' (%s)' % (pref)
        return name
    def sortname_pref(self):
        return "%s, %s" % (self.last_name, self.first_with_pref())
    def name_with_pref(self):
        return "%s %s" % (self.first_with_pref(), self.last_name)
    def letter_name(self):
        if 'letter_name' in self.config:
            return self.config['letter_name']
        else:
            return self.name()
    def get_title(self):
        if 'title' in self.config:
            return self.config['title']
        elif self.title:
            return self.title
        elif 'gender' in self.config and self.config['gender'] == 'M':
            return 'Mr'
        elif 'gender' in self.config and self.config['gender'] == 'F':
            return 'Ms'
        else:
            return 'M'
    def email(self):
        if 'email' in self.config:
            return self.config['email']
        elif self.userid:
            return "%s@sfu.ca" % (self.userid)
        elif 'applic_email' in self.config:
            return self.config['applic_email']
        else:
            return None
    def userid_or_emplid(self):
        "userid if possible or emplid if not: inverse of find_userid_or_emplid searching"
        return self.userid or self.emplid

    def __cmp__(self, other):
        return cmp((self.last_name, self.first_name, self.userid), (other.last_name, other.first_name, other.userid))
    class Meta:
        verbose_name_plural = "People"
        ordering = ['last_name', 'first_name', 'userid']
    
    def delete(self, *args, **kwargs):
        raise NotImplementedError, "This object cannot be deleted because it is used as a foreign key."
    
    def email_mailto(self):
        "A mailto: URL for this person's email address: handles the case where we don't know an email for them."
        email = self.email()
        if email:
            return mark_safe('<a href="mailto:%s">%s</a>' % (escape(email), escape(email)))
        else:
            return "None"
    def search_label_value(self):
        return "%s (%s), %s" % (self.name(), self.userid, self.emplid)

    @staticmethod
    def next_available_temp_emplid():
        p = Person.objects.filter(temporary=True).order_by('-emplid')
        if len(p) == 0:
            return 133700001
        else:
            return p[0].emplid + 1

    @staticmethod
    def next_available_temp_userid():
        return "tmp-"+str(Person.next_available_temp_emplid())[5:]


class Semester(models.Model):
    """
    A semester object: not imported, must be created manually.
    """
    label_lookup = {
        '1': 'Spring',
        '4': 'Summer',
        '7': 'Fall',
        }
    slug_lookup = {
        '1': 'sp',
        '4': 'su',
        '7': 'fa',
        }
    months_lookup = {
        '1': 'Jan-Apr',
        '4': 'May-Aug',
        '7': 'Sep-Dec',
        }
    name = models.CharField(max_length=4, null=False, db_index=True, unique=True,
        help_text='Semester name should be in the form "1097".')
    start = models.DateField(help_text='First day of classes.')
    end = models.DateField(help_text='Last day of classes.')

    class Meta:
        ordering = ['name']
    def __cmp__(self, other):
        return cmp(self.name, other.name)
    def sem_number(self):
        "number of semesters since spring 1900 (for subtraction)"
        yr = int(self.name[0:3])
        sm = int(self.name[3])
        if sm == 1:
            return 3*yr + 0
        elif sm == 4:
            return 3*yr + 1
        elif sm == 7:
            return 3*yr + 2
        else:
            raise ValueError, "Unknown semester number"
    def __sub__(self, other):
        "Number of semesters between the two args"
        return self.sem_number() - other.sem_number()

    def delete(self, *args, **kwargs):
        raise NotImplementedError, "This object cannot be deleted because it is used as a foreign key."
    
    def label(self):
        """
        The human-readable label for the semester, e.g. "Summer 2010".
        """
        name = str(self.name)
        if len(name) < 3 or len(name) > 4:
            return "Invalid"
        if len(name) == 3:
            name = "0"+name
        if name[1] == '8':
            name = "0" + name[1:]
        year = 1900 + int(name[0:3])
        semester = self.label_lookup[name[3]]
        return semester + " " + str(year)
    def months(self):
        return self.months_lookup[self.name[3]]
    def slugform(self):
        """
        The slug version of the semester, e.g. "2010su".
        """
        name = str(self.name)
        year = 1900 + int(name[0:3])
        semester = self.slug_lookup[name[3]]
        return str(year) + semester

    def __unicode__(self):
        return self.label()
    
    def timely(self):
        """
        Is this semester temporally relevant (for display in menu)?
        """
        today = datetime.date.today()
        month_ago = today - datetime.timedelta(days=40)
        ten_days_ago = today + datetime.timedelta(days=10)
        return self.end > month_ago and self.start < ten_days_ago
    
    def week_weekday(self, dt, weeks=None):
        """
        Given a datetime, return the week-of-semester and day-of-week (with 0=Monday).
        """
        # gracefully deal with both date and datetime objects
        if isinstance(dt, datetime.datetime):
            date = dt.date()
        else:
            date = dt

        # find the "base": first known week before the given date
        if not weeks:
            weeks = list(SemesterWeek.objects.filter(semester=self))
        weeks.reverse()
        base = None
        for w in weeks:
            if w.monday <= date:
                base = w
                break

        if base is None:
            ##raise ValueError, "Date seems to be before the start of semester."
            # might as well do something with the before-semester case.
            return 1,0

        diff = date - base.monday
        diff = int(round(diff.days + diff.seconds / 86400.0) + 0.5) # convert to number of days, rounding off any timezone stuff
        week = base.week + diff // 7
        wkday = date.weekday()
        return week, wkday
    
    def duedate(self, wk, wkday, time):
        """
        Calculate duedate based on week-of-semester and weekday.  Provided argument time can be either datetime.time or datetime.datetime: time is copied from this to new duedate.
        """
        # find the "base": first known week before mk
        weeks = list(SemesterWeek.objects.filter(semester=self))
        weeks.reverse()
        base = None
        for w in weeks:
            if w.week <= wk:
                base = w
                break
        
        date = base.monday + datetime.timedelta(days=7 * (wk - base.week) + wkday)
        # construct the datetime from date and time.
        if time:
            dt = datetime.datetime(year=date.year, month=date.month, day=date.day,
                hour=time.hour, minute=time.minute, second=time.second,
                microsecond=time.microsecond, tzinfo=time.tzinfo)
        else:
            dt = datetime.date(year=date.year, month=date.month, day=date.day)
        return dt

    def previous_semester(self):
        "semester before this one"
        return self.offset(-1)
    def next_semester(self):
        "semester after this one"
        return self.offset(1)
    
    def offset(self, n):
        "The semester n semesters forward/back in time"
        if n > 0:
            try:
                return Semester.objects.filter(name__gt=self.name).order_by('name')[n-1]
            except IndexError:
                return None
        elif n < 0:
            try:
                return Semester.objects.filter(name__lt=self.name).order_by('-name')[(-n)-1]
            except IndexError:
                return None
        else:
            return self

    def offset_name(self, n):
        "as offset() but only calculate the semester.name, without querying the DB"
        OFFSET_LOOKUP = { # key: (current semester, sem offset), value: (year offset, new semester)
            (1, 0): (0, 1),
            (1, 1): (0, 4),
            (1, 2): (0, 7),
            (4, 0): (0, 4),
            (4, 1): (0, 7),
            (4, 2): (1, 1),
            (7, 0): (0, 7),
            (7, 1): (1, 1),
            (7, 2): (1, 4),
        }

        yrs, sems = divmod(n, 3)

        name = self.name
        year = 1900 + int(name[0:3])
        sem = int(name[3])

        yroff, newsem = OFFSET_LOOKUP[sem, sems]
        year += yrs + yroff

        return "%03i%s" % (year-1900, newsem)

    
    @classmethod
    def current(cls):
        return cls.get_semester()

    @staticmethod
    def start_end_dates(semester):
        """
        First and last days of the semester, in the way that financial people do (without regard to class start/end dates)
        """
        yr = int(semester.name[0:3]) + 1900
        sm = int(semester.name[3])
        if sm == 1:
            start = datetime.date(yr, 1, 1)
            end = datetime.date(yr, 4, 30)
        elif sm == 4:
            start = datetime.date(yr, 5, 1)
            end = datetime.date(yr, 8, 31)
        elif sm == 7:
            start = datetime.date(yr, 9, 1)
            end = datetime.date(yr, 12, 31)
        return start, end

    @classmethod
    def get_semester(cls, date=None):
        if not date:
            date = datetime.date.today()
        return Semester.objects.filter(start__lte=date).order_by('-start')[0]

    @classmethod
    def next_starting(cls):
        """
        The next semester that starts after now
        """
        today = datetime.date.today()
        sems = Semester.objects.filter(start__gt=today).order_by('start')
        if sems:
            return sems[0]
        else:
            # just in case there's nothing in the future
            sems = Semester.objects.order_by('-start')
            return sems[0]

    @classmethod
    def first_relevant(cls):
        """
        The first semester that's relevant for most reporting: first semester that ends after two months ago.
        """
        today = datetime.date.today()
        year = today.year
        if 1 <= today.month <= 2:
            # last fall semester
            year -= 1
            sem = 7
        elif 3 <= today.month <= 6:
            # this spring
            sem = 1
        elif 7 <= today.month <= 10:
            # this summer
            sem = 4
        elif 11 <= today.month <= 12:
            # this fall
            sem = 7
        
        name = "%03d%1d" % ((year - 1900), sem)
        return Semester.objects.get(name=name)

    @classmethod
    def range(cls, start, end):
        """
        Produce a list of semesters from start to end. 
        Semester.range( '1134', '1147' ) == [ '1134', '1137', '1141', '1144', '1147' ]

        Will run forever or fail if the end semester is not a valid semester. 
        """
        current_semester = Semester.objects.get(name=start)
        while current_semester and current_semester.name != end:
            yield str(current_semester.name)
            current_semester = current_semester.offset(1) 
        if current_semester:
            yield str(current_semester.name)

class SemesterWeek(models.Model):
    """
    Starting points for weeks in the semester.
    
    Every semester object needs at least a SemesterWeek for week 1.
    """
    semester = models.ForeignKey(Semester, null=False)
    week = models.PositiveSmallIntegerField(null=False, help_text="Week of the semester (typically 1-13)")
    monday = models.DateField(help_text='Monday of this week.')
    
    def __unicode__(self):
        return "%s week %i" % (self.semester.name, self.week)
    class Meta:
        ordering = ['semester', 'week']
        unique_together = (('semester', 'week'))


HOLIDAY_TYPE_CHOICES = (
        ('FULL', 'Classes cancelled, offices closed'),
        ('CLAS', 'Classes cancelled, offices open'),
        ('OPEN', 'Classes as scheduled'),
        )

class Holiday(models.Model):
    """
    A holiday to display on the calendar (and possibly exclude classes on that day).
    """
    date = models.DateField(help_text='Date of the holiday', null=False, blank=False, db_index=True)
    semester = models.ForeignKey(Semester, null=False)
    description = models.CharField(max_length=30, null=False, blank=False, help_text='Description of holiday, e.g. "Canada Day"')
    holiday_type = models.CharField(max_length=4, null=False, choices=HOLIDAY_TYPE_CHOICES,
        help_text='Type of holiday: how does it affect schedules?')
    def __unicode__(self):
        return "%s on %s" % (self.description, self.date)
    class Meta:
        ordering = ['date']


class Course(models.Model, ConditionalSaveMixin):
    """
    More abstract model for a course.
    
    Note that title (and possibly stuff in config) might change over time:
    values in CourseOffering should be used where available.
    """
    subject = models.CharField(max_length=4, null=False, db_index=True,
        help_text='Subject code, like "CMPT" or "FAN".')
    number = models.CharField(max_length=4, null=False, db_index=True,
        help_text='Course number, like "120" or "XX1".')
    title = models.CharField(max_length=30, help_text='The course title.')
    config = JSONField(null=False, blank=False, default={}) # addition configuration stuff
    def autoslug(self):
        return make_slug(self.subject + '-' + self.number)
    slug = AutoSlugField(populate_from=autoslug, null=False, editable=False, unique=True)
    
    class Meta:
        unique_together = (('subject', 'number'),)
        ordering = ('subject', 'number')
    def __unicode__(self):
        return "%s %s" % (self.subject, self.number)
    def __cmp__(self, other):
        return cmp(self.subject, other.subject) or cmp(self.number, other.number)
    def delete(self, *args, **kwargs):
        raise NotImplementedError, "This object cannot be deleted because it is used as a foreign key."
    def full_name(self):
        return "%s %s - %s" % (self.subject, self.number, self.title)


COMPONENT_CHOICES = (
        ('LEC', 'Lecture'),
        ('LAB', 'Lab'),
        ('TUT', 'Tutorial'),
        ('SEM', 'Seminar'),
        ('SEC', 'Section'), # "Section"?  ~= lecture?
        ('PRA', 'Practicum'),
        ('IND', 'Individual Work'),
        ('INS', 'INS'), # ???
        ('WKS', 'Workshop'),
        ('FLD', 'Field School'),
        ('STD', 'Studio'),
        ('OLC', 'OLC'), # ???
        ('RQL', 'RQL'), # ???
        ('STL', 'STL'), # ???
        ('CNV', 'CNV'), # converted from SIMON?
        ('OPL', 'Open Lab'), # ???
        ('CAN', 'Cancelled')
        )
COMPONENTS = dict(COMPONENT_CHOICES)
CAMPUS_CHOICES = (
        ('BRNBY', 'Burnaby Campus'),
        ('SURRY', 'Surrey Campus'),
        ('VANCR', 'Harbour Centre'),
        ('OFFST', 'Off-campus'),
        #('SEGAL', 'Segal Centre'),
        ('GNWC', 'Great Northern Way Campus'),
        #('KAM', 'Kamloops Campus'),
        ('METRO', 'Other Locations in Vancouver'),
        )
CAMPUS_CHOICES_SHORT = (
        ('BRNBY', 'Burnaby'),
        ('SURRY', 'Surrey'),
        ('VANCR', 'Harbour Ctr'),
        ('OFFST', 'Off-campus'),
        #('SEGAL', 'Segal Ctr'),
        ('GNWC', 'Great North. Way'),
        #('KAM', 'Kamloops'),
        ('METRO', 'Other Vancouver'),
        )
CAMPUSES = dict(CAMPUS_CHOICES)
CAMPUSES_SHORT = dict(CAMPUS_CHOICES_SHORT)
OFFERING_FLAGS = [
    ('write', 'W'),
    ('quant', 'Q'),
    ('bhum', 'B-Hum'),
    ('bsci', 'B-Sci'),
    ('bsoc', 'B-Soc'),
    ('combined', 'Combined section'), # used to flag sections that have been merged in the import
    ]
OFFERING_FLAG_KEYS = [flag[0] for flag in OFFERING_FLAGS]
WQB_FLAGS = [(k,v) for k,v in OFFERING_FLAGS if k != 'combined']
WQB_KEYS = [flag[0] for flag in WQB_FLAGS]
WQB_DICT = dict(WQB_FLAGS)
INSTR_MODE_CHOICES = [ # from ps_instruct_mode in reporting DB
    ('CO', 'Co-Op'),
    ('DE', 'Distance Education'),
    ('GI', 'Graduate Internship'),
    ('P', 'In Person'),
    ('PO', 'In Person - Off Campus'),
    ]
INSTR_MODE = dict(INSTR_MODE_CHOICES)

class CourseOffering(models.Model, ConditionalSaveMixin):
    subject = models.CharField(max_length=4, null=False, db_index=True,
        help_text='Subject code, like "CMPT" or "FAN"')
    number = models.CharField(max_length=4, null=False, db_index=True,
        help_text='Course number, like "120" or "XX1"')
    section = models.CharField(max_length=4, null=False, db_index=True,
        help_text='Section should be in the form "C100" or "D100"')
    semester = models.ForeignKey(Semester, null=False)
    component = models.CharField(max_length=3, null=False, choices=COMPONENT_CHOICES, db_index=True,
        help_text='Component of the offering, like "LEC" or "LAB"')
    instr_mode = models.CharField(max_length=2, null=False, choices=INSTR_MODE_CHOICES, default='P', db_index=True,
        help_text='The instructional mode of the offering')
    graded = models.BooleanField(default=True)
    owner = models.ForeignKey('Unit', null=True, help_text="Unit that controls this offering")
    # need these to join in the SIMS database: don't care otherwise.
    crse_id = models.PositiveSmallIntegerField(null=True, db_index=True)
    class_nbr = models.PositiveIntegerField(null=True, db_index=True)

    title = models.CharField(max_length=30, help_text='The course title', db_index=True)
    campus = models.CharField(max_length=5, choices=CAMPUS_CHOICES, db_index=True)
    enrl_cap = models.PositiveSmallIntegerField()
    enrl_tot = models.PositiveSmallIntegerField()
    wait_tot = models.PositiveSmallIntegerField()
    units = models.PositiveSmallIntegerField(null=True, help_text='The number of credits received by (most?) students in the course')
    course = models.ForeignKey(Course, null=False)

    # WQB requirement flags
    flags = BitField(flags=OFFERING_FLAG_KEYS, default=0)
    
    members = models.ManyToManyField(Person, related_name="member", through="Member")
    config = JSONField(null=False, blank=False, default={}) # addition configuration stuff
        # 'url': URL of course home page
        # 'department': department responsible for course (used by discipline module)
        # 'taemail': TAs' contact email (if not their personal email)
        # 'labtut': are there lab sections? (default False)
        # 'labtas': TAs get the LAB_BONUS lab/tutorial bonus (default False)
        # 'uses_svn': create SVN repos for this course? (default False)
        # 'indiv_svn': do instructors/TAs have access to student SVN repos? (default False)
        # 'instr_rw_svn': can instructors/TAs *write* to student SVN repos? (default False)
        # 'group_min': minimum group size
        # 'group_max': maximum group size
        # 'extra_bu': number of TA base units required
        # 'page_creators': who is allowed to create new pages?
        # 'sessional_pay': amount the sessional was paid (used in grad finances)
        # 'joint_with': list of offerings this one is combined with (as CourseOffering.slug)
        # 'maillist': course mailing list (@sfu.ca). Used for CMPT in course homepage list

    defaults = {'taemail': None, 'url': None, 'labtut': False, 'labtas': False, 'indiv_svn': False,
                'uses_svn': False, 'extra_bu': '0', 'page_creators': 'STAF', 'discussion': False,
                'instr_rw_svn': False, 'joint_with': (), 'group_min': None, 'group_max': None,
                'maillist': None}
    labtut, set_labtut = getter_setter('labtut')
    _, set_labtas = getter_setter('labtas')
    url, set_url = getter_setter('url')
    taemail, set_taemail = getter_setter('taemail')
    indiv_svn, set_indiv_svn = getter_setter('indiv_svn')
    instr_rw_svn, set_instr_rw_svn = getter_setter('instr_rw_svn')
    extra_bu_str, set_extra_bu_str = getter_setter('extra_bu')
    page_creators, set_page_creators = getter_setter('page_creators')
    discussion, set_discussion = getter_setter('discussion')
    _, set_sessional_pay = getter_setter('sessional_pay')
    joint_with, set_joint_with = getter_setter('joint_with')
    group_min, set_group_min = getter_setter('group_min')
    group_max, set_group_max = getter_setter('group_max')
    _, set_maillist = getter_setter('maillist')
    copy_config_fields = ['url', 'taemail', 'indiv_svn', 'page_creators', 'discussion', 'uses_svn', 'instr_rw_svn',
                          'group_min', 'group_max'] # fields that should be copied when instructor does "copy course setup"
    
    def autoslug(self):
        # changed slug format for fall 2011
        if self.semester.name >= "1117":
            if self.section[2:4] == "00":
                words = [str(s).lower() for s in self.semester.slugform(), self.subject, self.number, self.section[:2]]
            else:
                # these shouldn't be in the DB anymore, but there are a few left, so handle them
                words = [str(s).lower() for s in self.semester.slugform(), self.subject, self.number, self.section]
        else:
            words = [str(s).lower() for s in self.semester.name, self.subject, self.number, self.section]
        return '-'.join(words)
    slug = AutoSlugField(populate_from=autoslug, null=False, editable=False, unique=True)

    class Meta:
        ordering = ['-semester', 'subject', 'number', 'section']
        unique_together = (
            ('semester', 'subject', 'number', 'section'),
            ('semester', 'crse_id', 'section'),
            ('semester', 'class_nbr'))

    def __unicode__(self):
        return "%s %s %s (%s)" % (self.subject, self.number, self.section, self.semester.label())
    def name(self):
        if self.graded and self.section[2:4] == '00':
            return "%s %s %s" % (self.subject, self.number, self.section[:-2])
        else:
            return "%s %s %s" % (self.subject, self.number, self.section)
    
    def save(self, *args, **kwargs):
        # make sure CourseOfferings always have .course filled.
        if not self.course_id:
            self.set_course(save=False)
        super(CourseOffering, self).save(*args, **kwargs)
    
    def get_absolute_url(self):
        return reverse('grades.views.course_info', kwargs={'course_slug': self.slug})
    
    def instructors(self):
        return (m.person for m in self.member_set.filter(role="INST").select_related('person'))
    def instructors_str(self):
        @cached(60*60*24*2)
        def _instr_str(pk):
            return '; '.join(p.sortname() for p in CourseOffering.objects.get(pk=pk).instructors())
        return _instr_str(self.pk)

    def tas(self):
        return (m.person for m in self.member_set.filter(role="TA"))
    def student_count(self):
        return self.members.filter(person__role='STUD').count()
    def combined(self):
        return self.flags.combined
    def set_combined(self, val):
        self.flags.combined = val

    def get_campus_display(self):
        # override to handle the distance ed special case
        if self.instr_mode == 'DE':
            return 'Distance Education'
        if self.campus in CAMPUSES:
            return CAMPUSES[self.campus]
        else:
            return 'unknown'
    def get_campus_short_display(self):
        if self.instr_mode == 'DE':
            return 'Distance'
        if self.campus in CAMPUSES_SHORT:
            return CAMPUSES_SHORT[self.campus]
        else:
            return 'unknown'

    def maillist(self):
        """
        The slug used in the CMPT course mailing list scheme
        """
        if 'maillist' in self.config and self.config['maillist']:
            return self.config['maillist']

        @cached(60*60*24*7)
        def _maillist(pk):
            o = CourseOffering.objects.get(pk=pk)
            num = o.number.replace('W', '')
            others = CourseOffering.objects \
                .filter(subject=o.subject, number__in=[num, num+'W'], semester_id=o.semester_id) \
                .exclude(pk=pk).exclude(component='CAN').exists()
            if others:
                return '%s-%s-%s' % (o.subject.lower(), num, o.section[0:2].lower())
            else:
                return '%s-%s' % (o.subject.lower(), num)

        return _maillist(self.pk)

    
    def get_wqb_display(self):
        flags = [WQB_DICT[f] for f,v in self.flags.iteritems() if v and f in WQB_KEYS]
        if flags:
            return ', '.join(flags)
        else:
            return 'none'

    def extra_bu(self):
        return decimal.Decimal(self.extra_bu_str())
    def set_extra_bu(self, v):
        assert isinstance(v, decimal.Decimal)
        self.set_extra_bu_str(str(v))
    def labtas(self):
        """
        Handles the logic for LAB_BONUS base units.
        
        Default yes if there are lab/tutorial sections; default no otherwise. 
        """
        if 'labtas' in self.config:
            return self.config['labtas']
        elif 'labtut' in self.config and self.config['labtut']:
            return True
        else:
            return False
    def sessional_pay(self):
        """
        Pay for the sessional instructor: check self.owner for a value if not set on offering.
        """
        if 'sessional_pay' in self.config:
            return decimal.Decimal(self.config['sessional_pay'])
        elif 'sessional_pay' in self.owner.config:
            return decimal.Decimal(self.owner.config['sessional_pay'])
        else:
            return 0
    
    def set_course(self, save=True):
        """
        Set this objects .course field to a sensible value, creating a Course object if necessary.
        """
        cs = Course.objects.filter(subject=self.subject, number=self.number)
        if cs:
            self.course = cs[0]
        else:
            c = Course(subject=self.subject, number=self.number, title=self.title)
            c.save()
            self.course = c
        
        if save:
            self.save()
    
    def uses_svn(self):
        """
        Should students and groups in this course get Subversion repositories created?
        """
        if 'uses_svn' in self.config and self.config['uses_svn']:
            return True

        return self.subject == "CMPT" \
            and ((self.semester.name == "1117" and self.number in ["470", "379", "882"])
                 or (self.semester.name >= "1121" and self.number >= "200"))

    
    def export_dict(self):
        """
        Produce dictionary of data about offering that can be serialized as JSON
        """
        d = {}
        d['subject'] = self.subject
        d['number'] = self.number
        d['section'] = self.section
        d['semester'] = self.semester.name
        d['component'] = self.component
        d['title'] = self.title
        d['campus'] = self.campus
        d['meetingtimes'] = [m.export_dict() for m in self.meetingtime_set.all()]
        d['instructors'] = [{'userid': m.person.userid, 'name': m.person.name()} for m in self.member_set.filter(role="INST").select_related('person')]
        d['wqb'] = [desc for flag,desc in WQB_FLAGS if getattr(self.flags, flag)]
        d['class_nbr'] = self.class_nbr
        return d
    
    def delete(self, *args, **kwargs):
        raise NotImplementedError, "This object cannot be deleted because it is used as a foreign key."

    def __cmp__(self, other):
        return cmp(other.semester.name, self.semester.name) \
            or cmp(self.subject, other.subject) \
            or cmp(self.number, other.number) \
            or cmp(self.section, other.section)
    def search_label_value(self):
        return "%s (%s)" % (self.name(), self.semester.label())
        

class Member(models.Model, ConditionalSaveMixin):
    """
    "Members" of the course.  Role indicates instructor/student/TA/etc.

    Includes dropped students and non-graded sections (labs/tutorials).  Often want to select with:
        Member.objects.exclude(role="DROP").filter(...)
    """
    ROLE_CHOICES = (
        ('STUD', 'Student'),
        ('TA', 'TA'),
        ('INST', 'Instructor'),
        ('APPR', 'Grade Approver'),
        ('DROP', 'Dropped'),
        #('AUD', 'Audit Student'),
    )
    REASON_CHOICES = (
        ('AUTO', 'Automatically added'),
        ('TRU', 'TRU/OU Distance Student'),
        ('CTA', 'CourSys-Appointed TA'), # from ta app
        ('TAC', 'CourSys-Appointed TA'), # from tacontracts app
        ('TA', 'Additional TA'),
        ('TAIN', 'TA added by instructor'),
        ('INST', 'Additional Instructor'),
        ('UNK', 'Unknown/Other Reason'),
    )
    CAREER_CHOICES = (
        ('UGRD', 'Undergraduate'),
        ('GRAD', 'Graduate'),
        ('NONS', 'Non-Student'),
    )
    CAREERS = dict(CAREER_CHOICES)
    person = models.ForeignKey(Person, related_name="person")
    offering = models.ForeignKey(CourseOffering)
    role = models.CharField(max_length=4, choices=ROLE_CHOICES)
    credits = models.PositiveSmallIntegerField(null=False, default=3,
        help_text='Number of credits this course is worth.')
    career = models.CharField(max_length=4, choices=CAREER_CHOICES)
    added_reason = models.CharField(max_length=4, choices=REASON_CHOICES, db_index=True)
    labtut_section = models.CharField(max_length=4, null=True, blank=True,
        help_text='Section should be in the form "C101" or "D103".')
    official_grade = models.CharField(max_length=2, null=True, blank=True)
    config = JSONField(null=False, blank=False, default={}) # addition configuration stuff:
        # 'origsection': The originating section (for crosslisted sections combined here)
        #     represented as a CourseOffering.slug
        #     default: self.offering (if accessed by m.get_origsection())
        # 'bu': The number of BUs this TA has
        # 'teaching_credit': The number of teaching credits instructor receives for this offering. Fractions stored as strings: '1/3'
        # 'teaching_credit_reason': reason for the teaching credit override
        # 'last_discuss': Last view of the offering's discussion forum (seconds from epoch)

    defaults = {'bu': 0, 'teaching_credit': 1, 'teaching_credit_reason': None, 'last_discuss': 0}
    raw_bu, set_bu = getter_setter('bu')
    last_discuss, set_last_discuss = getter_setter('last_discuss')
    
    def __unicode__(self):
        return "%s (%s) in %s" % (self.person.userid, self.person.emplid, self.offering,)
    def short_str(self):
        return "%s (%s)" % (self.person.name(), self.person.userid)
    def delete(self, *args, **kwargs):
        raise NotImplementedError, "This object cannot be deleted because it is used as a foreign key."
    def clean(self):
        """
        Validate unique_together = (('person', 'offering', 'role'),) UNLESS role=='DROP'
        """
        if self.role == 'DROP':
            return
        if not hasattr(self, 'person'):
            raise ValidationError('No person set.')
        others = Member.objects.filter(person=self.person, offering=self.offering, role=self.role)
        if self.pk:
            others = others.exclude(pk=self.pk)

        if others:
            raise ValidationError('There is another membership with this person, offering, and role.  These must be unique for a membership (unless role is "dropped").')

    def bu(self):
        return decimal.Decimal(unicode(self.raw_bu()))

    @classmethod
    def get_memberships(cls, userid):
        """
        Get course memberships for this userid that we want to display on their menu. return list of Member objects and
        a boolean indicating whether or not there were temporal exclusions (so the "course history" link is relevant).
        """
        today = datetime.date.today()
        past1 = today - datetime.timedelta(days=365) # 1 year ago
        past2 = today - datetime.timedelta(days=730) # 2 years ago
        memberships = Member.objects.exclude(role="DROP").exclude(offering__component="CAN") \
                .filter(offering__graded=True, person__userid=userid) \
                .annotate(num_activities=Count('offering__activity')) \
                .annotate(num_pages=Count('offering__page')) \
                .select_related('offering','offering__semester')
        memberships = list(memberships) # get out of the database and do this locally

        # students don't see non-active courses or future courses
        memberships = [m for m in memberships if
                        m.role in ['TA', 'INST', 'APPR']
                        or ((m.num_activities > 0 or m.num_pages > 0)
                            and m.offering.semester.start <= today)]

        count1 = len(memberships)
        # exclude everything from more than 2 years ago
        memberships = [m for m in memberships if m.offering.semester.end >= past2]

        # students don't see as far in the past
        memberships = [m for m in memberships if
                        m.role in ['TA', 'INST', 'APPR']
                        or m.offering.semester.end >= past1]
        count2 = len(memberships)

        # have courses been excluded because of date?
        excluded = (count1-count2) != 0
        return memberships, excluded


    def teaching_credit(self):
        """
        Number of teaching credits this is worth, as a Fraction.
        """
        return self.teaching_credit_with_reason()[0]

    def teaching_credit_with_reason(self):
        """
        Number of teaching credits this is worth, as a Fraction, along with a short explanation for the value
        """
        assert self.role=='INST' and self.added_reason=='AUTO' # we can only sensibly calculate this for SIMS instructors

        if 'teaching_credit' in self.config:
            # if manually set, then honour it
            if 'teaching_credit_reason' in self.config:
                reason = self.config['teaching_credit_reason']
                if len(reason) > 15:
                    reason = 'set manually: ' + reason[:15] + u'\u2026'
                else:
                    reason = 'set manually: ' + reason
            else:
                reason = 'set manually'
            return fractions.Fraction(self.config['teaching_credit']), reason
        elif self.offering.enrl_tot == 0:
            # no students => no teaching credit (probably a cancelled section we didn't catch on import)
            return fractions.Fraction(0), 'empty section'
        elif self.offering.instr_mode == 'DE':
            # No credit for distance-ed supervision
            return fractions.Fraction(0), 'distance ed'
        elif self.offering.instr_mode in ['CO', 'GI']:
            # No credit for co-op, grad-internship, distance-ed supervision
            return fractions.Fraction(0), 'co-op'
        elif MeetingTime.objects.filter(offering=self.offering, meeting_type__in=['LEC']).count() == 0:
            # no lectures probably means directed studies or similar
            return fractions.Fraction(0), 'no scheduled lectures'
        else:
            # now probably a real offering: split the credit among the (real SIMS) instructors and across joint offerings
            joint_with = CourseOffering.objects.filter(slug__in=self.offering.joint_with())
            other_instr = Member.objects.filter(offering=self.offering, role='INST', added_reason='AUTO').exclude(id=self.id)
            credits = fractions.Fraction(1)
            reasons = []
            if other_instr:
                other_instr = list(other_instr)
                credits /= len(other_instr) + 1
                reasons.append('co-taught with %s' % (', '.join(m.person.name() for m in other_instr)))
            if joint_with:
                joint_with = list(joint_with)
                credits /= len(joint_with) + 1
                reasons.append('joint with %s' % (', '.join(o.name() for o in joint_with)))
            #if self.offering.units is not None and self.offering.units != 3:
            #    # TODO: is this consistent with other units on campus? Do some have a different default credit count?
            #    credits *= fractions.Fraction(self.offering.units, 3)
            #    reasons.append('%i unit course' % (self.offering.units))
            return credits, '; '.join(reasons)

    def set_teaching_credit(self, cred):
        assert isinstance(cred, fractions.Fraction) or isinstance(cred, int)
        self.config['teaching_credit'] = unicode(cred)

    def set_teaching_credit_reason(self, reason):
        self.config['teaching_credit_reason'] = unicode(reason)

    def get_tug(self):
        assert self.role == 'TA'
        from ta.models import TUG

        tugs = TUG.objects.filter(member=self)
        if tugs:
            tug = tugs[0]
        else:
            tug = None
        return tug


    def svn_url(self):
        "SVN URL for this member (assuming offering.uses_svn())"
        return urlparse.urljoin(settings.SVN_URL_BASE, repo_name(self.offering, self.person.userid))

    def get_origsection(self):
        """
        The real CourseOffering for this student (for crosslisted sections combined in this system).
        """
        if 'origsection' in self.config:
            return CourseOffering.objects.get(slug=self.config['origsection'])
        else:
            return self.offering
    
    class Meta:
        #unique_together = (('person', 'offering', 'role'),)  # now handled by self.clean()
        ordering = ['offering', 'person']
    def get_absolute_url(self):
        return reverse('grades.views.student_info', kwargs={'course_slug': self.offering.slug,
                                                            'userid': self.person.userid_or_emplid()})
    
    @classmethod
    def clear_old_official_grades(cls):
        """
        Clear out the official grade field on old records: no need to tempt fate.
        """
        cutoff = datetime.date.today() - datetime.timedelta(days=120)
        old_grades = Member.objects.filter(offering__semester__end__lt=cutoff, official_grade__isnull=False)
        old_grades.update(official_grade=None)


WEEKDAY_CHOICES = (
        (0, 'Monday'),
        (1, 'Tuesday'),
        (2, 'Wednesday'),
        (3, 'Thursday'),
        (4, 'Friday'),
        (5, 'Saturday'),
        (6, 'Sunday'),
        )
WEEKDAYS = dict(WEEKDAY_CHOICES)
MEETINGTYPE_CHOICES = (
        ("LEC", "Lecture"),
        ("MIDT", "Midterm Exam"),
        ("EXAM", "Exam"),
        ("LAB", "Lab/Tutorial"),
        )
MEETINGTYPES = dict(MEETINGTYPE_CHOICES)
class MeetingTime(models.Model):
    offering = models.ForeignKey(CourseOffering, null=False)
    weekday = models.PositiveSmallIntegerField(null=False, choices=WEEKDAY_CHOICES,
        help_text='Day of week of the meeting')
    start_time = models.TimeField(null=False, help_text='Start time of the meeting')
    end_time = models.TimeField(null=False, help_text='End time of the meeting')
    start_day = models.DateField(null=False, help_text='Starting day of the meeting')
    end_day = models.DateField(null=False, help_text='Ending day of the meeting')
    room = models.CharField(max_length=20, help_text='Room (or other location) for the meeting')
    exam = models.BooleanField(default=False) # unused: use meeting_type instead
    meeting_type = models.CharField(max_length=4, choices=MEETINGTYPE_CHOICES, default="LEC")
    labtut_section = models.CharField(max_length=4, null=True, blank=True,
        help_text='Section should be in the form "C101" or "D103".  None/blank for the non lab/tutorial events.')
    def __unicode__(self):
        return "%s %s %s-%s" % (unicode(self.offering), WEEKDAYS[self.weekday], self.start_time, self.end_time)

    class Meta:
        ordering = ['weekday']
        #unique_together = (('offering', 'weekday', 'start_time'), ('offering', 'weekday', 'end_time'))
    
    def export_dict(self):
        """
        Produce dictionary of data about meeting time that can be serialized as JSON
        """
        d = {}
        d['weekday'] = self.weekday
        d['start_day'] = str(self.start_day)
        d['end_day'] = str(self.end_day)
        d['start_time'] = str(self.start_time)
        d['end_time'] = str(self.end_time)
        d['room'] = self.room
        d['type'] = self.meeting_type
        return d

class Unit(models.Model):
    """
    An academic unit within the university: a department/school/faculty.
    
    Unit with label=='UNIV' is used for global roles
    """
    label = models.CharField(max_length=4, null=False, blank=False, db_index=True, unique=True,
            help_text="The unit code, e.g. 'CMPT'.")
    name = models.CharField(max_length=60, null=False, blank=False,
           help_text="The full name of the unit, e.g. 'School of Computing Science'.")
    parent = models.ForeignKey('Unit', null=True, blank=True,
             help_text="Next unit up in the hierarchy.")
    acad_org = models.CharField(max_length=10, null=True, blank=True, db_index=True, unique=True, help_text="ACAD_ORG field from SIMS")
    def autoslug(self):
        return self.label.lower()
    slug = AutoSlugField(populate_from=autoslug, null=False, editable=False, unique=True)
    config = JSONField(null=False, blank=False, default={}) # addition configuration stuff:
        # 'address': list of (3) lines in mailing address (default: SFU main address)
        # 'email': contact email address (may be None)
        # 'web': URL
        # 'tel': contact phone number
        # 'fax': fax number (may be None)
        # 'deptid': department ID for finances
        # 'informal_name': formal name of the unit (e.g. "Computing Science")
        # 'sessional_pay': default amount sessionals are paid (used in grad finances)
        # 'card_account':  Account code for card access forms
        # 'card_rooms': Rooms all grads have access to; separate lines with "|" and buildings/rooms with ":", e.g. "AQ:1234|AQ:5678"
    
    defaults = {'address': ['8888 University Drive', 'Burnaby, BC', 'Canada V5A 1S6'],
                'email': None, 'tel': '778.782.3111', 'fax': None, 'web': 'http://www.sfu.ca/',
                'deptid': ''}
    address, set_address = getter_setter('address')
    email, set_email = getter_setter('email')
    tel, set_tel = getter_setter('tel')
    fax, set_fax = getter_setter('fax')
    web, set_web = getter_setter('web')
    deptid, set_deptid = getter_setter('deptid')
    _, set_informal_name = getter_setter('informal_name')

    class Meta:
        ordering = ['label']
    def __unicode__(self):
        return "%s (%s)" % (self.name, self.label)
    def delete(self, *args, **kwargs):
        raise NotImplementedError, "This object cannot be deleted because it is used as a foreign key."
    
    def informal_name(self):
        if 'informal_name' in self.config and self.config['informal_name']:
            return self.config['informal_name']
        else:
            return self.name
    
    def uses_fasnet(self):
        """
        Used to decide whether or not to display the FASnet account forms.
        """
        return self.slug in ['cmpt', 'ensc']
    
    @classmethod
    #@cached(24*3600)
    def __sub_unit_ids(cls, unitids):
        """
        Do the actual work for sub_unit_ids
        """
        children = unitids
        decendants = set(children)
        while True:
            children = Unit.objects.filter(parent__in=children).values('id')
            children = set(u['id'] for u in children)
            if not children:
                break
            decendants |= children
        return decendants

    @classmethod
    def sub_unit_ids(cls, units, by_id=False):
        """
        Get the Unit.id values for all descendants of the given list of unit.id values.

        Cached so we can avoid the work when possible.
        """
        # sort unit.id values for consistent cache keys
        if by_id:
            unitids = sorted(list(set(units)))
        else:
            unitids = sorted(list(set(u.id for u in units)))
        return Unit.__sub_unit_ids(unitids)

    @classmethod
    def sub_units(cls, units, by_id=False):
        ids = cls.sub_unit_ids(units, by_id=by_id)
        return Unit.objects.filter(id__in=ids)


    def __super_units(self):
        if not self.parent:
            return []
        else:
            return [self.parent] + self.parent.super_units()
            
    def super_units(self):
        """
        Units directly above this in the heirarchy
        """
        key = 'superunits-' + self.slug
        res = cache.get(key)
        if res:
            return res
        else:
            res = self.__super_units()
            cache.set(key, res, 24*3600)
            return res
        


ROLE_CHOICES = (
        ('ADVS', 'Advisor'),
        ('FAC', 'Faculty Member'),
        ('SESS', 'Sessional Instructor'),
        ('COOP', 'Co-op Staff'),
        ('INST', 'Other Instructor'),
        ('SUPV', 'Additional Supervisor'),
        ('PLAN', 'Planning Administrator'),
        ('DISC', 'Discipline Case Administrator'),
        ('DICC', 'Discipline Case Filer (email CC)'),
        ('ADMN', 'Departmental Administrator'),
        ('TAAD', 'TA Administrator'),
        ('TADM', 'Teaching Administrator'),
        ('GRAD', 'Grad Student Administrator'),
        ('GRPD', 'Graduate Program Director'),
        ('FUND', 'Grad Funding Administrator'),
        ('TECH', 'Tech Staff'),
        ('GPA', 'GPA conversion system admin'),
        ('SYSA', 'System Administrator'),
        ('NONE', 'none'),
        )
ROLES = dict(ROLE_CHOICES)
# roles departmental admins ('ADMN') are allowed to assign within their unit
UNIT_ROLES = ['ADVS', 'DISC', 'DICC', 'TAAD', 'GRAD', 'FUND', 'GRPD',
              'FAC', 'SESS', 'COOP', 'INST', 'SUPV'] # 'PLAN', 'TADM', 'TECH'
# help text for the departmental admin on those roles
ROLE_DESCR = {
        'ADVS': 'Has access to the advisor notes.',
        'DISC': 'Can manage academic discipline cases in the unit: should include your Academic Integrity Coordinator.',
        'DICC': 'Will be copied on all discipline case letters in the unit: include whoever files your discipline cases.',
        'PLAN': 'Can manage plans for course offerings in future semesters.',
        'TAAD': 'Can administer TA job postings and appointments.',
        'TADM': 'Can manage teaching history for faculty members.',
        'GRAD': 'Can view and update the grad student database.',
        'GRPD': 'Director of the graduate program: typically the signer of grad-related letters.',
        'FUND': 'Can work with the grad student funding database.',
        'TECH': 'Can manage resources required for courses.',
        'FAC': 'Faculty Member',
        'SESS': 'Sessional Instructor',
        'COOP': 'Co-op Staff Member',
        'INST': 'Instructors outside of the department or others who teach courses',
        'REPR': 'Has Reporting Database access.',
        'SUPV': 'Others who can supervise RAs or grad students, in addition to faculty',
              }
INSTR_ROLES = ["FAC","SESS","COOP",'INST'] # roles that are given to categorize course instructors

class Role(models.Model):
    """
    Additional roles within the system (not course-related).
    """
    ROLES = dict(ROLE_CHOICES)
    person = models.ForeignKey(Person)
    role = models.CharField(max_length=4, choices=ROLE_CHOICES)
    unit = models.ForeignKey(Unit)
    config = JSONField(null=False, blank=False, default={}) # addition configuration stuff:
        # 'gone': used with role='FAC' to indicate this person has left/retired/whatever

    gone = config_property('gone', False)

    def __unicode__(self):
        return "%s (%s, %s)" % (self.person, self.ROLES[str(self.role)], self.unit.label)
    class Meta:
        unique_together = (('person', 'role', 'unit'),)

    @classmethod
    def all_roles(cls, userid):
        return set((r.role for r in Role.objects.filter(person__userid=userid)))

    
class ComputingAccount(models.Model):
    """
    A model to represent userid <-> emplid mappings.
    
    Complete table imported from AMAINT so we can lookup online. i.e. if the user
    has an active computing account, there should be an entry here. (There is no
    such guarantee for Person.)
    """
    emplid = models.PositiveIntegerField(primary_key=True, unique=True, null=False)
    userid = models.CharField(max_length=8, unique=True, null=False, db_index=True)


class CombinedOffering(models.Model):
    """
    A model to represent a local fake CourseOffering that should be the result of combining members of two or more
    CourseOffering objects on import.
    """
    subject = models.CharField(max_length=4, null=False, blank=False)
    number = models.CharField(max_length=4, null=False, blank=False)
    section = models.CharField(max_length=4, null=False, blank=False)
    semester = models.ForeignKey(Semester, null=False, blank=False)
    component = models.CharField(max_length=3, null=False, choices=COMPONENT_CHOICES)
    instr_mode = models.CharField(max_length=2, null=False, choices=INSTR_MODE_CHOICES, default='P')
    owner = models.ForeignKey('Unit', null=True, help_text="Unit that controls this offering")
    crse_id = models.PositiveSmallIntegerField(null=True)
    class_nbr = models.PositiveIntegerField(null=True) # fake value for DB constraint on CourseOffering

    title = models.CharField(max_length=30)
    campus = models.CharField(max_length=5, choices=CAMPUS_CHOICES)

    offerings = models.ManyToManyField(CourseOffering, related_name='+')
        # actually a Many-to-One, but don't want to junk CourseOffering up with another ForeignKey

    config = JSONField(null=False, blank=False, default={}) # addition configuration stuff

    def name(self):
        return "%s %s %s" % (self.subject, self.number, self.section)

    def create_combined_offering(self):
        """
        Do the import-like work of creating/updating the CourseOffering object
        """
        with transaction.atomic():
            try:
                offering = CourseOffering.objects.get(semester=self.semester, subject=self.subject,
                        number=self.number, section=self.section)
            except CourseOffering.DoesNotExist:
                offering = CourseOffering(semester=self.semester, subject=self.subject,
                        number=self.number, section=self.section)
                offering.enrl_cap = 0
                offering.enrl_tot = 0
                offering.wait_tot = 0
                offering.set_combined(True)
                offering.save()

            offering.component = self.component
            offering.instr_mode = self.instr_mode
            offering.owner = self.owner
            offering.crse_id = self.crse_id
            offering.class_nbr = self.class_nbr
            offering.title = self.title
            offering.campus = self.campus

            cap_total = 0
            tot_total = 0
            wait_total = 0
            labtut = False
            in_section = set() # students who are in section and not dropped (so we don't overwrite with a dropped membership)
            for sub in self.offerings.all():
                cap_total += sub.enrl_cap
                tot_total += sub.enrl_tot
                wait_total += sub.wait_tot
                labtut = labtut or sub.labtut()
                for m in sub.member_set.all():
                    old_ms = offering.member_set.filter(offering=offering, person=m.person)
                    if old_ms:
                        # was already a member: update.
                        old_m = old_ms[0]
                        old_m.role = m.role
                        old_m.credits = m.credits
                        old_m.career = m.career
                        old_m.added_reason = m.added_reason
                        old_m.config['origsection'] = sub.slug
                        old_m.labtut_section = m.labtut_section
                        if m.role != 'DROP' or old_m.person_id not in in_section:
                            # condition keeps from overwriting enrolled students with drops (from other section)
                            old_m.save()
                        if m.role != 'DROP':
                            in_section.add(old_m.person_id)
                    else:
                        # new membership: duplicate into combined
                        new_m = Member(offering=offering, person=m.person, role=m.role, labtut_section=m.labtut_section,
                                credits=m.credits, career=m.career, added_reason=m.added_reason)
                        new_m.config['origsection'] = sub.slug
                        new_m.save()
                        if m.role != 'DROP':
                            in_section.add(new_m.person_id)

            offering.enrl_cap = cap_total
            offering.enrl_tot = tot_total
            offering.wait_tot = wait_total
            offering.set_labtut(labtut)
            offering.save()