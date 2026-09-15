import uuid

from django.conf import settings
from django.core.validators import MaxLengthValidator
from django.db import models


class PersonalDatabase(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    owner = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    database_name = models.CharField(max_length=63, unique=True, editable=False)
    role_name = models.CharField(max_length=63, unique=True, editable=False)
    status = models.CharField(max_length=16, default="pending")
    error_code = models.CharField(max_length=64, blank=True)
    onboarding_completed = models.BooleanField(default=False)
    # Owner-authored preferences about when and how the assistant should save.
    # They refine assistant behaviour; they never widen database permissions.
    saving_instructions = models.TextField(
        blank=True, default="", validators=[MaxLengthValidator(4000)]
    )
    saving_instructions_updated_at = models.DateTimeField(
        null=True, blank=True, editable=False
    )

    def __str__(self):
        return self.database_name

    def save(self, *args, **kwargs):
        self.database_name = f"odb_{self.id.hex}"
        self.role_name = f"odb_owner_{self.id.hex}"
        super().save(*args, **kwargs)


class AccessRole(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    database = models.ForeignKey(PersonalDatabase, on_delete=models.CASCADE)
    name = models.CharField(max_length=100)
    description = models.TextField(
        blank=True, default="", validators=[MaxLengthValidator(2000)]
    )

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["database", "name"], name="role_name_per_db"
            )
        ]

    def __str__(self):
        return self.name

    @property
    def pg_name(self):
        return f"odb_group_{self.id.hex}"


class RoleAssignment(models.Model):
    role = models.ForeignKey(AccessRole, on_delete=models.CASCADE)
    email = models.EmailField()

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["role", "email"], name="role_email_unique")
        ]

    def __str__(self):
        return self.email


class ObjectGrant(models.Model):
    role = models.ForeignKey(AccessRole, on_delete=models.CASCADE)
    object_name = models.CharField(max_length=63)
    relation_oid = models.PositiveBigIntegerField(null=True, editable=False)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["role", "relation_oid"], name="role_relation_unique"
            )
        ]

    def __str__(self):
        return self.object_name
