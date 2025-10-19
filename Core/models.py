from decimal import Decimal

from django.contrib.auth.models import AbstractUser
from django.core.validators import MinValueValidator
from django.db import models
from django.utils import timezone

# ----------------------------
# Usuario con rol propio
# ----------------------------
class User(AbstractUser):
    ROLES = (
        ("ADMIN", "Administrador"),
        ("VET", "Veterinario"),
        ("ADMIN_OP", "Administrativo"),
        ("OWNER", "Propietario"),
    )
    rol = models.CharField(max_length=20, choices=ROLES)
    telefono = models.CharField(max_length=20, blank=True)
    direccion = models.CharField(max_length=200, blank=True)
    activo = models.BooleanField(default=True)
    avatar = models.ImageField(upload_to="avatars/", blank=True, null=True)
    especialidad = models.CharField(max_length=100, blank=True)  # para veterinarios

    def __str__(self):
        return f"{self.username} ({self.get_rol_display()})"

# ----------------------------
# Propietario
# ----------------------------
class Propietario(models.Model):
    user = models.OneToOneField(User, on_delete=models.CASCADE)
    telefono = models.CharField(max_length=20, blank=True)
    direccion = models.CharField(max_length=200, blank=True)
    ciudad = models.CharField(max_length=100, blank=True)
    notas = models.TextField(blank=True)

    def __str__(self):
        return self.user.get_full_name() or self.user.username

# ----------------------------
# Paciente / Mascota
# ----------------------------
class Paciente(models.Model):
    nombre = models.CharField(max_length=100)
    especie = models.CharField(max_length=50)
    raza = models.CharField(max_length=50, blank=True)
    sexo = models.CharField(max_length=10)
    fecha_nacimiento = models.DateField()
    propietario = models.ForeignKey(Propietario, on_delete=models.CASCADE)
    vacunas = models.TextField(blank=True)
    alergias = models.TextField(blank=True)
    foto = models.ImageField(upload_to="pacientes/", blank=True, null=True)

    def __str__(self):
        return f"{self.nombre} ({self.especie})"

# ----------------------------
# Cita
# ----------------------------
class Cita(models.Model):
    ESTADOS = (
        ("pendiente", "Pendiente"),
        ("programada", "Programada"),
        ("atendida", "Atendida"),
        ("cancelada", "Cancelada"),
    )
    TIPOS = (
        ("consulta", "Consulta"),
        ("vacunacion", "Vacunación"),
        ("cirugia", "Cirugía"),
    )
    paciente = models.ForeignKey(Paciente, on_delete=models.CASCADE)
    veterinario = models.ForeignKey(User, limit_choices_to={"rol": "VET"}, on_delete=models.SET_NULL, null=True)
    fecha_hora = models.DateTimeField()
    duracion = models.IntegerField(default=30)  # duración en minutos
    tipo = models.CharField(max_length=50, choices=TIPOS, default="consulta")
    estado = models.CharField(max_length=20, choices=ESTADOS, default="pendiente")
    notas = models.TextField(blank=True)

    def __str__(self):
        veterinario_nombre = self.veterinario.username if self.veterinario else "Sin asignar"
        fecha_local = self.fecha_hora
        if timezone.is_aware(self.fecha_hora):
            fecha_local = timezone.localtime(self.fecha_hora)
        return (
            f"Cita: {self.paciente.nombre} ({self.get_estado_display()}) con {veterinario_nombre} - "
            f"{fecha_local.strftime('%d/%m/%Y %H:%M')}"
        )

# ----------------------------
# Historial Médico
# ----------------------------
class HistorialMedico(models.Model):
    paciente = models.ForeignKey(Paciente, on_delete=models.CASCADE)
    veterinario = models.ForeignKey(User, limit_choices_to={"rol": "VET"}, on_delete=models.SET_NULL, null=True)
    fecha = models.DateTimeField(auto_now_add=True)
    diagnostico = models.TextField()
    tratamiento = models.TextField()
    notas = models.TextField(blank=True)
    peso = models.DecimalField(max_digits=5, decimal_places=2, blank=True, null=True)
    temperatura = models.DecimalField(max_digits=4, decimal_places=1, blank=True, null=True)
    examenes = models.TextField(blank=True)
    imagenes = models.ImageField(upload_to="historial_medico/", blank=True, null=True)
    proximo_control = models.DateField(blank=True, null=True)

    def __str__(self):
        return f"Historial de {self.paciente.nombre} - {self.fecha.strftime('%d/%m/%Y')}"


class Producto(models.Model):
    CATEGORIAS = (
        ("alimentos", "Alimentos"),
        ("medicamentos", "Medicamentos"),
        ("accesorios", "Accesorios"),
    )

    nombre = models.CharField(max_length=150)
    descripcion = models.TextField()
    categoria = models.CharField(max_length=30, choices=CATEGORIAS)
    precio = models.DecimalField(
        max_digits=8,
        decimal_places=2,
        validators=[MinValueValidator(Decimal("0.01"))],
    )
    imagen = models.ImageField(upload_to="productos/", blank=True, null=True)
    disponible = models.BooleanField(default=True)
    creado = models.DateTimeField(auto_now_add=True)
    actualizado = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-actualizado"]

    def __str__(self):
        return self.nombre


class VacunaRecomendada(models.Model):
    ESPECIES = (
        ("canino", "Canino"),
        ("felino", "Felino"),
    )

    UNIDADES_TIEMPO = (
        ("semanas", "Semanas"),
        ("meses", "Meses"),
        ("anios", "Años"),
    )

    nombre = models.CharField(max_length=150)
    especie = models.CharField(max_length=20, choices=ESPECIES)
    descripcion = models.TextField(blank=True)
    edad_recomendada = models.PositiveIntegerField()
    unidad_tiempo = models.CharField(max_length=10, choices=UNIDADES_TIEMPO)
    refuerzo = models.CharField(max_length=150, blank=True)
    orden = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ["especie", "orden", "nombre"]
        unique_together = ("especie", "nombre")

    def __str__(self):
        return f"{self.nombre} ({self.get_especie_display()})"

    def edad_legible(self) -> str:
        unidad = self.get_unidad_tiempo_display().lower()
        valor = self.edad_recomendada
        if valor == 1 and unidad.endswith("s"):
            unidad = unidad[:-1]
        return f"{valor} {unidad}"


class VacunaRegistro(models.Model):
    paciente = models.ForeignKey(
        Paciente,
        on_delete=models.CASCADE,
        related_name="registros_vacunas",
    )
    vacuna = models.ForeignKey(
        VacunaRecomendada,
        on_delete=models.CASCADE,
        related_name="registros",
    )
    fecha_aplicacion = models.DateField()
    notas = models.TextField(blank=True)
    creado = models.DateTimeField(auto_now_add=True)
    actualizado = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-fecha_aplicacion", "-actualizado"]
        unique_together = ("paciente", "vacuna")

    def __str__(self):
        return f"{self.vacuna.nombre} - {self.paciente.nombre} ({self.fecha_aplicacion:%d/%m/%Y})"
