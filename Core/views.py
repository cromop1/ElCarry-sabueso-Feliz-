from datetime import datetime

from django.contrib import messages
from django.contrib.auth import authenticate, login, logout
from django.contrib.auth.decorators import login_required
from django.db import connection
from django.db.models import Count, Q
from django.db.utils import OperationalError, ProgrammingError
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone

from .forms import ProductoForm, VacunaRegistroForm
from .models import (
    Cita,
    HistorialMedico,
    Paciente,
    Producto,
    Propietario,
    User,
    VacunaRecomendada,
    VacunaRegistro,
)


def _producto_table_available() -> bool:
    """Return True if the Producto table exists in the configured database."""

    table_name = Producto._meta.db_table
    try:
        return table_name in connection.introspection.table_names()
    except (OperationalError, ProgrammingError):
        return False


def _vacunas_tables_available() -> bool:
    required_tables = {
        VacunaRecomendada._meta.db_table,
        VacunaRegistro._meta.db_table,
    }
    try:
        tables = set(connection.introspection.table_names())
    except (OperationalError, ProgrammingError):
        return False
    return required_tables.issubset(tables)


def _normalizar_especie_mascota(especie: str) -> str:
    valor = (especie or "").strip().lower()
    if valor.startswith("perr") or valor.startswith("can"):
        return "canino"
    if valor.startswith("gat") or valor.startswith("fel"):
        return "felino"
    return ""


# ----------------------------
# Sitio público
# ----------------------------

def landing(request):
    productos_destacados = Producto.objects.none()
    total_productos = 0
    productos_disponibles = _producto_table_available()

    if productos_disponibles:
        productos_destacados = Producto.objects.filter(disponible=True)[:6]
        total_productos = Producto.objects.filter(disponible=True).count()

    return render(
        request,
        "core/landing.html",
        {
            "productos_destacados": productos_destacados,
            "total_productos": total_productos,
        },
    )


def tienda(request):
    categoria = request.GET.get("categoria")
    busqueda = request.GET.get("q", "").strip()

    productos = Producto.objects.none()

    productos_disponibles = _producto_table_available()

    if productos_disponibles:
        productos = Producto.objects.filter(disponible=True)
        if categoria in dict(Producto.CATEGORIAS):
            productos = productos.filter(categoria=categoria)
        if busqueda:
            productos = productos.filter(
                Q(nombre__icontains=busqueda) | Q(descripcion__icontains=busqueda)
            )

        productos = productos.order_by("nombre")

    return render(
        request,
        "core/tienda.html",
        {
            "productos": productos,
            "categoria_activa": categoria,
            "busqueda": busqueda,
            "categorias": Producto.CATEGORIAS,
        },
    )


def detalle_producto(request, producto_id):
    if not _producto_table_available():
        messages.error(
            request,
            "La tienda aún no está configurada. Ejecuta las migraciones pendientes para administrar productos.",
        )
        return redirect("landing")

    queryset = Producto.objects.filter(disponible=True)
    if request.user.is_authenticated and request.user.rol == "ADMIN":
        queryset = Producto.objects.all()
    producto = get_object_or_404(queryset, id=producto_id)
    relacionados = (
        Producto.objects.filter(disponible=True, categoria=producto.categoria)
        .exclude(id=producto.id)
        .order_by("-actualizado")[:4]
    )
    return render(
        request,
        "core/detalle_producto.html",
        {"producto": producto, "relacionados": relacionados},
    )


# ----------------------------
# Dashboard y estadísticas
# ----------------------------


@login_required
def dashboard(request):
    user = request.user
    context = {}

    if user.rol == "ADMIN":
        context["total_usuarios"] = User.objects.count()
        context["total_pacientes"] = Paciente.objects.count()
        context["total_citas"] = Cita.objects.count()
        context["total_historiales"] = HistorialMedico.objects.count()
        productos_disponibles = _producto_table_available()
        context["total_productos"] = Producto.objects.count() if productos_disponibles else 0
        resumen = {estado: 0 for estado, _ in Cita.ESTADOS}
        for item in Cita.objects.values("estado").annotate(total=Count("id")):
            resumen[item["estado"]] = item["total"]
        context["resumen_citas"] = resumen
        context["todas_citas"] = (
            Cita.objects.select_related(
                "paciente", "paciente__propietario__user", "veterinario"
            ).order_by("-fecha_hora")[:20]
        )
        context["todos_pacientes"] = (
            Paciente.objects.select_related("propietario__user").order_by("nombre")[:20]
        )
        context["productos_recientes"] = (
            Producto.objects.order_by("-actualizado")[:6]
            if productos_disponibles
            else Producto.objects.none()
        )
    elif user.rol == "VET":
        context["mis_citas"] = Cita.objects.filter(veterinario=user).order_by(
            "-fecha_hora"
        )
        context["mis_historiales"] = HistorialMedico.objects.filter(
            veterinario=user
        ).order_by("-fecha")
    elif user.rol == "OWNER":
        propietario = get_object_or_404(Propietario, user=user)

        mascotas = list(
            Paciente.objects.filter(propietario=propietario).order_by("nombre")
        )
        citas_queryset = (
            Cita.objects.filter(paciente__propietario=propietario)
            .select_related("paciente", "veterinario")
            .order_by("fecha_hora")
        )
        citas = list(citas_queryset)
        historiales_queryset = (
            HistorialMedico.objects.filter(paciente__propietario=propietario)
            .select_related("paciente", "veterinario")
            .order_by("-fecha")
        )
        historiales = list(historiales_queryset)

        ahora = timezone.now()
        citas_proximas = [c for c in citas if c.fecha_hora >= ahora]
        citas_pasadas = [c for c in citas if c.fecha_hora < ahora]
        citas_pasadas.sort(key=lambda cita: cita.fecha_hora, reverse=True)

        context.update(
            {
                "mis_mascotas": mascotas,
                "mis_citas": list(reversed(citas)),
                "mis_historiales": historiales,
                "proxima_cita": citas_proximas[0] if citas_proximas else None,
                "citas_proximas": citas_proximas[:5],
                "citas_recientes": citas_pasadas[:5],
                "historiales_recientes": historiales[:5],
                "estadisticas_propietario": {
                    "mascotas": len(mascotas),
                    "citas_activas": len(citas_proximas),
                    "informes": len(historiales),
                    "profesionales": len(
                        {c.veterinario_id for c in citas if c.veterinario_id}
                    ),
                },
            }
        )

        productos_disponibles = _producto_table_available()
        context["productos_sugeridos"] = (
            Producto.objects.filter(disponible=True)
            .order_by("-actualizado")[:3]
            if productos_disponibles
            else Producto.objects.none()
        )
    elif user.rol == "ADMIN_OP":
        context["todas_citas"] = Cita.objects.all().order_by("-fecha_hora")
        context["todos_pacientes"] = Paciente.objects.all()

    return render(request, "core/dashboard.html", context)


# ----------------------------
# Mascotas y propietarios
# ----------------------------


@login_required
def calendario_vacunas(request):
    if request.user.rol != "OWNER":
        messages.error(request, "Acceso exclusivo para propietarios.")
        return redirect("dashboard")

    propietario = get_object_or_404(Propietario, user=request.user)
    mascotas = list(
        Paciente.objects.filter(propietario=propietario).order_by("nombre")
    )

    vacunas_disponibles = _vacunas_tables_available()
    mascota_seleccionada = None
    mascota_id = request.POST.get("paciente_id") or request.GET.get("paciente")

    if mascotas:
        try:
            mascota_id_int = int(mascota_id) if mascota_id else None
        except (TypeError, ValueError):
            mascota_id_int = None

        for mascota in mascotas:
            if mascota_id_int is not None and mascota.id == mascota_id_int:
                mascota_seleccionada = mascota
                break
        if mascota_seleccionada is None:
            mascota_seleccionada = mascotas[0]
            mascota_id_int = mascota_seleccionada.id
    else:
        mascota_id_int = None

    if request.method == "POST":
        if not mascotas:
            messages.error(
                request,
                "Registra una mascota para comenzar a gestionar su calendario de vacunas.",
            )
            return redirect("registrar_mascota")

        if not vacunas_disponibles:
            messages.error(
                request,
                "El módulo de vacunas todavía no está disponible. Ejecuta las migraciones pendientes para activarlo.",
            )
            return redirect("calendario_vacunas")

        form = VacunaRegistroForm(request.POST)
        accion = request.POST.get("accion")
        redirect_base = reverse("calendario_vacunas")
        redirect_actual = (
            f"{redirect_base}?paciente={mascota_seleccionada.id}"
            if mascota_seleccionada
            else redirect_base
        )

        if form.is_valid():
            paciente_id = form.cleaned_data["paciente_id"]
            vacuna_id = form.cleaned_data["vacuna_id"]
            fecha = form.cleaned_data.get("fecha_aplicacion") or timezone.localdate()
            notas = form.cleaned_data.get("notas", "").strip()

            paciente_obj = next(
                (m for m in mascotas if m.id == paciente_id),
                None,
            )
            if paciente_obj is None:
                messages.error(request, "La mascota seleccionada no es válida.")
                return redirect(redirect_actual)

            redirect_url = f"{redirect_base}?paciente={paciente_obj.id}"

            vacuna_obj = VacunaRecomendada.objects.filter(id=vacuna_id).first()
            if vacuna_obj is None:
                messages.error(request, "La vacuna indicada no existe.")
                return redirect(redirect_url)

            especie_paciente = _normalizar_especie_mascota(paciente_obj.especie)
            if not especie_paciente:
                messages.error(
                    request,
                    "La especie de la mascota no cuenta con un calendario configurado.",
                )
                return redirect(redirect_url)

            if vacuna_obj.especie != especie_paciente:
                messages.error(
                    request,
                    "La vacuna seleccionada no corresponde a la especie de la mascota.",
                )
                return redirect(redirect_url)

            if accion == "marcar":
                registro, creado = VacunaRegistro.objects.update_or_create(
                    paciente=paciente_obj,
                    vacuna=vacuna_obj,
                    defaults={
                        "fecha_aplicacion": fecha,
                        "notas": notas,
                    },
                )
                if creado:
                    messages.success(
                        request,
                        f"Se registró la aplicación de {vacuna_obj.nombre} para {paciente_obj.nombre}.",
                    )
                else:
                    messages.success(
                        request,
                        f"Se actualizó la aplicación de {vacuna_obj.nombre} para {paciente_obj.nombre}.",
                    )
            elif accion == "desmarcar":
                eliminados, _ = VacunaRegistro.objects.filter(
                    paciente=paciente_obj, vacuna=vacuna_obj
                ).delete()
                if eliminados:
                    messages.info(
                        request,
                        f"Se eliminó el registro de {vacuna_obj.nombre} para {paciente_obj.nombre}.",
                    )
                else:
                    messages.warning(
                        request,
                        "No se encontró un registro previo para eliminar.",
                    )
            else:
                messages.error(request, "Acción no reconocida.")
            return redirect(redirect_url)

        errors = ", ".join(
            [str(error) for error_list in form.errors.values() for error in error_list]
        )
        if errors:
            messages.error(request, errors)
        return redirect(redirect_actual)

    especie_normalizada = (
        _normalizar_especie_mascota(mascota_seleccionada.especie)
        if mascota_seleccionada
        else ""
    )

    vacunas_recomendadas = []
    registros_por_vacuna = {}

    if vacunas_disponibles and mascota_seleccionada and especie_normalizada:
        vacunas_recomendadas = list(
            VacunaRecomendada.objects.filter(especie=especie_normalizada).order_by(
                "orden", "nombre"
            )
        )
        registros_por_vacuna = {
            registro.vacuna_id: registro
            for registro in VacunaRegistro.objects.filter(
                paciente=mascota_seleccionada,
                vacuna__in=vacunas_recomendadas,
            )
        }

    vacunas_info = [
        {
            "vacuna": vacuna,
            "registro": registros_por_vacuna.get(vacuna.id),
        }
        for vacuna in vacunas_recomendadas
    ]

    total_vacunas = len(vacunas_recomendadas)
    completadas = sum(1 for item in vacunas_info if item["registro"])
    porcentaje_avance = int((completadas / total_vacunas) * 100) if total_vacunas else 0

    return render(
        request,
        "core/calendario_vacunas.html",
        {
            "mascotas": mascotas,
            "mascota_seleccionada": mascota_seleccionada,
            "vacunas_info": vacunas_info,
            "especie_normalizada": especie_normalizada,
            "vacunas_disponibles": vacunas_disponibles,
            "total_vacunas": total_vacunas,
            "vacunas_completadas": completadas,
            "vacunas_pendientes": max(total_vacunas - completadas, 0),
            "porcentaje_avance": porcentaje_avance,
            "hoy": timezone.localdate(),
        },
    )


@login_required
def mis_mascotas(request):
    propietario = get_object_or_404(Propietario, user=request.user)
    mascotas = Paciente.objects.filter(propietario=propietario)
    return render(request, "core/mis_mascotas.html", {"mascotas": mascotas})


@login_required
def detalle_mascota(request, paciente_id):
    paciente = get_object_or_404(Paciente, id=paciente_id)

    if request.user.rol == "OWNER" and paciente.propietario.user != request.user:
        messages.error(request, "No tienes permiso para ver esta mascota.")
        return redirect("dashboard")

    historiales_qs = HistorialMedico.objects.filter(paciente=paciente).order_by("-fecha")
    historiales = list(historiales_qs)

    citas_qs = (
        Cita.objects.filter(paciente=paciente)
        .select_related("veterinario")
        .order_by("-fecha_hora")
    )
    citas = list(citas_qs)

    historiales_por_fecha = {}
    for historial in historiales:
        fecha_hist = historial.fecha
        if timezone.is_aware(fecha_hist):
            fecha_hist = timezone.localtime(fecha_hist)
        historiales_por_fecha.setdefault(fecha_hist.date(), historial)

    for cita in citas:
        fecha_cita = cita.fecha_hora
        if timezone.is_aware(fecha_cita):
            fecha_cita = timezone.localtime(fecha_cita)
        cita.historial_relacionado = historiales_por_fecha.get(fecha_cita.date())

    ahora = timezone.now()
    citas_futuras = sorted(
        (cita for cita in citas if cita.fecha_hora >= ahora),
        key=lambda c: c.fecha_hora,
    )
    citas_pasadas = [cita for cita in citas if cita.fecha_hora < ahora]

    ultima_consulta = historiales[0] if historiales else None
    proxima_cita = citas_futuras[0] if citas_futuras else None

    template = (
        "core/detalle_mascota_admin.html"
        if request.user.rol in {"ADMIN", "ADMIN_OP"}
        else "core/detalle_mascota.html"
    )
    return render(
        request,
        template,
        {
            "paciente": paciente,
            "historiales": historiales,
            "citas": citas,
            "citas_futuras": citas_futuras,
            "citas_pasadas": citas_pasadas,
            "ultima_consulta": ultima_consulta,
            "proxima_cita": proxima_cita,
        },
    )


@login_required
def registrar_historial(request, paciente_id):
    paciente = get_object_or_404(Paciente, id=paciente_id)

    if request.user.rol != "VET":
        messages.error(request, "No tienes permiso para registrar historial médico.")
        return redirect("dashboard")

    if request.method == "POST":
        diagnostico = request.POST.get("diagnostico")
        tratamiento = request.POST.get("tratamiento")
        notas = request.POST.get("notas")
        peso = request.POST.get("peso") or None
        temperatura = request.POST.get("temperatura") or None
        examenes = request.POST.get("examenes")
        proximo_control = request.POST.get("proximo_control") or None

        HistorialMedico.objects.create(
            paciente=paciente,
            veterinario=request.user,
            diagnostico=diagnostico,
            tratamiento=tratamiento,
            notas=notas,
            peso=peso,
            temperatura=temperatura,
            examenes=examenes,
            proximo_control=proximo_control,
        )
        messages.success(request, "Historial médico registrado correctamente ✅")
        return redirect("detalle_mascota", paciente_id=paciente.id)

    return render(request, "core/registrar_historial.html", {"paciente": paciente})


@login_required
def listar_usuarios(request):
    if request.user.rol != "ADMIN":
        messages.error(request, "No tienes permiso para ver esta página.")
        return redirect("dashboard")

    usuarios = User.objects.all().order_by("username")
    return render(request, "core/usuarios.html", {"usuarios": usuarios})


@login_required
def listar_pacientes(request):
    if request.user.rol not in {"ADMIN", "ADMIN_OP"}:
        messages.error(request, "No tienes permiso para ver esta página.")
        return redirect("dashboard")

    pacientes = Paciente.objects.all().order_by("nombre")
    return render(request, "core/pacientes.html", {"pacientes": pacientes})


@login_required
def registrar_mascota(request):
    if request.user.rol != "OWNER":
        messages.error(request, "No tienes permiso para registrar mascotas.")
        return redirect("dashboard")

    propietario = get_object_or_404(Propietario, user=request.user)

    if request.method == "POST":
        has_error = False
        nombre = request.POST.get("nombre")
        especie = request.POST.get("especie")
        raza = request.POST.get("raza")
        sexo = request.POST.get("sexo")
        fecha_nacimiento = request.POST.get("fecha_nacimiento")

        fecha_obj = None
        if fecha_nacimiento:
            try:
                fecha_obj = datetime.strptime(fecha_nacimiento, "%Y-%m-%d").date()
            except ValueError:
                messages.error(request, "La fecha de nacimiento no es válida.")
                has_error = True
        else:
            messages.error(request, "Debes indicar la fecha de nacimiento.")
            has_error = True

        if not has_error:
            Paciente.objects.create(
                nombre=nombre,
                especie=especie,
                raza=raza,
                sexo=sexo,
                fecha_nacimiento=fecha_obj,
                propietario=propietario,
            )
            messages.success(
                request, f"Mascota {nombre} registrada correctamente ✅"
            )
            return redirect("mis_mascotas")

    return render(request, "core/registrar_mascota.html")


# ----------------------------
# Citas
# ----------------------------


@login_required
def mis_citas(request):
    user = request.user
    if user.rol == "VET":
        citas = Cita.objects.filter(veterinario=user).order_by("-fecha_hora")
    elif user.rol == "OWNER":
        propietario = get_object_or_404(Propietario, user=user)
        citas = Cita.objects.filter(paciente__propietario=propietario).order_by(
            "-fecha_hora"
        )
    elif user.rol in {"ADMIN_OP", "ADMIN"}:
        citas = Cita.objects.all().order_by("-fecha_hora")
    else:
        citas = Cita.objects.none()

    return render(request, "core/mis_citas.html", {"citas": citas})


@login_required
def agendar_cita(request, paciente_id=None):
    if request.user.rol != "OWNER":
        messages.error(request, "No tienes permiso para agendar citas.")
        return redirect("dashboard")

    propietario = get_object_or_404(Propietario, user=request.user)
    mascotas = Paciente.objects.filter(propietario=propietario)
    paciente_seleccionado = None

    if request.method == "POST":
        paciente_id_form = request.POST.get("paciente")
        fecha_hora_raw = request.POST.get("fecha_hora")
        notas = request.POST.get("notas", "").strip()

        paciente = get_object_or_404(
            Paciente, id=paciente_id_form, propietario=propietario
        )
        paciente_seleccionado = paciente

        if not fecha_hora_raw:
            messages.error(
                request, "Debes seleccionar una fecha y hora válidas para la cita."
            )
        else:
            try:
                fecha_hora_dt = datetime.fromisoformat(fecha_hora_raw)
            except ValueError:
                messages.error(request, "El formato de la fecha y hora no es válido.")
            else:
                if timezone.is_naive(fecha_hora_dt):
                    fecha_hora_dt = timezone.make_aware(
                        fecha_hora_dt, timezone.get_current_timezone()
                    )

                if fecha_hora_dt < timezone.now():
                    messages.error(
                        request,
                        "La fecha y hora seleccionadas ya pasaron. Elige un horario futuro.",
                    )
                else:
                    Cita.objects.create(
                        paciente=paciente,
                        fecha_hora=fecha_hora_dt,
                        notas=notas,
                        estado="pendiente",
                    )
                    messages.success(
                        request,
                        f"Solicitud registrada para {paciente.nombre}. Un administrador asignará un veterinario pronto.",
                    )
                    return redirect("mis_citas")

    if paciente_id and paciente_seleccionado is None:
        paciente_seleccionado = get_object_or_404(
            Paciente, id=paciente_id, propietario=propietario
        )

    return render(
        request,
        "core/agendar_cita.html",
        {"mascotas": mascotas, "paciente_seleccionado": paciente_seleccionado},
    )


@login_required
def asignar_veterinario_cita(request, cita_id):
    if request.user.rol not in {"ADMIN", "ADMIN_OP"}:
        messages.error(request, "No tienes permiso para asignar veterinarios a las citas.")
        return redirect("dashboard")

    cita = get_object_or_404(Cita, id=cita_id)
    veterinarios = User.objects.filter(rol="VET", activo=True).order_by(
        "first_name", "last_name"
    )

    if request.method == "POST":
        vet_id = request.POST.get("veterinario")
        if not vet_id:
            messages.error(request, "Debes seleccionar un veterinario para asignar la cita.")
        else:
            veterinario = get_object_or_404(User, id=vet_id, rol="VET")
            cita.veterinario = veterinario
            cita.estado = "programada"
            cita.save(update_fields=["veterinario", "estado"])
            nombre_vet = veterinario.get_full_name() or veterinario.username
            messages.success(
                request,
                f"Veterinario {nombre_vet} asignado correctamente a la cita ✅",
            )
            return redirect("listar_citas_admin")

    return render(
        request,
        "core/asignar_veterinario.html",
        {"cita": cita, "veterinarios": veterinarios},
    )


@login_required
def listar_citas_admin(request):
    if request.user.rol not in {"ADMIN", "ADMIN_OP"}:
        messages.error(request, "No tienes permiso para ver esta página.")
        return redirect("dashboard")

    citas_pendientes = (
        Cita.objects.select_related("paciente", "paciente__propietario__user")
        .filter(estado="pendiente")
        .order_by("fecha_hora")
    )
    citas_programadas = (
        Cita.objects.select_related(
            "paciente", "paciente__propietario__user", "veterinario"
        )
        .filter(estado="programada")
        .order_by("fecha_hora")
    )
    citas_atendidas = (
        Cita.objects.select_related(
            "paciente", "paciente__propietario__user", "veterinario"
        )
        .filter(estado="atendida")
        .order_by("-fecha_hora")
    )

    conteo_estados = {
        "pendiente": citas_pendientes.count(),
        "programada": citas_programadas.count(),
        "atendida": citas_atendidas.count(),
        "cancelada": Cita.objects.filter(estado="cancelada").count(),
    }

    return render(
        request,
        "core/citas_admin.html",
        {
            "citas_pendientes": citas_pendientes,
            "citas_programadas": citas_programadas,
            "citas_atendidas": citas_atendidas,
            "conteo_estados": conteo_estados,
        },
    )


@login_required
def asignar_veterinario_citas(request):
    if request.user.rol not in {"ADMIN", "ADMIN_OP"}:
        messages.error(request, "No tienes permiso para gestionar estas citas.")
        return redirect("dashboard")

    veterinarios = User.objects.filter(rol="VET", activo=True).order_by(
        "first_name", "last_name"
    )
    citas_pendientes = (
        Cita.objects.select_related("paciente", "paciente__propietario__user")
        .filter(estado="pendiente")
        .order_by("fecha_hora")
    )

    if request.method == "POST":
        cita_id = request.POST.get("cita")
        vet_id = request.POST.get("veterinario")

        if not cita_id or not vet_id:
            messages.error(request, "Selecciona una cita y un veterinario válidos.")
        else:
            cita = get_object_or_404(
                Cita, id=cita_id, estado__in=["pendiente", "programada"]
            )
            veterinario = get_object_or_404(User, id=vet_id, rol="VET")
            cita.veterinario = veterinario
            cita.estado = "programada"
            cita.save(update_fields=["veterinario", "estado"])
            nombre_vet = veterinario.get_full_name() or veterinario.username
            messages.success(
                request,
                f"Veterinario {nombre_vet} asignado correctamente a la cita de {cita.paciente.nombre} ✅",
            )
            return redirect("asignar_veterinario_citas")

    return render(
        request,
        "core/asignar_veterinario_citas.html",
        {"citas_pendientes": citas_pendientes, "veterinarios": veterinarios},
    )


@login_required
def atender_cita(request, cita_id):
    cita = get_object_or_404(Cita, id=cita_id)

    if request.user.rol != "VET":
        messages.error(request, "No tienes permiso para atender esta cita.")
        return redirect("dashboard")

    if request.method == "POST":
        diagnostico = request.POST.get("diagnostico")
        tratamiento = request.POST.get("tratamiento")
        notas = request.POST.get("notas")
        peso = request.POST.get("peso") or None
        temperatura = request.POST.get("temperatura") or None
        examenes = request.POST.get("examenes")
        proximo_control = request.POST.get("proximo_control") or None

        HistorialMedico.objects.create(
            paciente=cita.paciente,
            veterinario=request.user,
            diagnostico=diagnostico,
            tratamiento=tratamiento,
            notas=notas,
            peso=peso,
            temperatura=temperatura,
            examenes=examenes,
            proximo_control=proximo_control,
        )

        cita.estado = "atendida"
        cita.save(update_fields=["estado"])

        messages.success(
            request, f"Cita de {cita.paciente.nombre} atendida correctamente ✅"
        )
        return redirect("detalle_cita", cita_id=cita.id)

    return render(request, "core/atender_cita.html", {"cita": cita})


@login_required
def mis_historiales(request):
    if request.user.rol != "VET":
        messages.error(request, "No tienes permiso para ver esta página.")
        return redirect("dashboard")

    historiales = HistorialMedico.objects.filter(veterinario=request.user).order_by(
        "-fecha"
    )
    return render(request, "core/mis_historiales.html", {"historiales": historiales})


@login_required
def detalle_cita(request, cita_id):
    cita = get_object_or_404(Cita, id=cita_id)

    if request.user.rol == "OWNER" and cita.paciente.propietario.user != request.user:
        messages.error(request, "No tienes permiso para ver esta cita.")
        return redirect("dashboard")

    fecha_cita = cita.fecha_hora
    if timezone.is_aware(fecha_cita):
        fecha_cita = timezone.localtime(fecha_cita)

    historial = (
        HistorialMedico.objects.filter(
            paciente=cita.paciente, fecha__date=fecha_cita.date()
        )
        .order_by("-fecha")
        .first()
    )

    if not historial:
        historial = (
            HistorialMedico.objects.filter(paciente=cita.paciente)
            .order_by("-fecha")
            .first()
        )

    return render(request, "core/detalle_cita.html", {"cita": cita, "historial": historial})


@login_required
def agendar_cita_admin(request):
    if request.user.rol != "ADMIN":
        messages.error(request, "No tienes permiso para agendar citas.")
        return redirect("dashboard")

    mascotas = Paciente.objects.all().order_by("nombre")
    veterinarios = User.objects.filter(rol="VET").order_by("first_name", "last_name")
    paciente_seleccionado = None

    if request.method == "POST":
        paciente_id = request.POST.get("paciente")
        veterinario_id = request.POST.get("veterinario")
        fecha_hora_raw = request.POST.get("fecha_hora")
        notas = request.POST.get("notas", "").strip()

        paciente = get_object_or_404(Paciente, id=paciente_id)
        veterinario = get_object_or_404(User, id=veterinario_id, rol="VET")

        try:
            fecha_hora_dt = datetime.fromisoformat(fecha_hora_raw)
        except (TypeError, ValueError):
            messages.error(request, "Selecciona una fecha y hora válidas.")
        else:
            if timezone.is_naive(fecha_hora_dt):
                fecha_hora_dt = timezone.make_aware(
                    fecha_hora_dt, timezone.get_current_timezone()
                )

            if fecha_hora_dt < timezone.now():
                messages.error(request, "No puedes programar una cita en el pasado.")
            else:
                Cita.objects.create(
                    paciente=paciente,
                    veterinario=veterinario,
                    fecha_hora=fecha_hora_dt,
                    notas=notas,
                    estado="programada",
                )
                nombre_vet = veterinario.get_full_name() or veterinario.username
                messages.success(
                    request,
                    f"Cita para {paciente.nombre} asignada a {nombre_vet} ✅",
                )
                return redirect("dashboard")

        paciente_seleccionado = paciente

    return render(
        request,
        "core/agendar_cita_admin.html",
        {
            "mascotas": mascotas,
            "veterinarios": veterinarios,
            "paciente_seleccionado": paciente_seleccionado,
        },
    )


@login_required
def crear_propietario_admin(request):
    if request.user.rol != "ADMIN":
        messages.error(request, "No tienes permiso para esta acción.")
        return redirect("dashboard")

    if request.method == "POST":
        username = request.POST.get("username")
        email = request.POST.get("email")
        first_name = request.POST.get("first_name")
        last_name = request.POST.get("last_name")
        telefono = request.POST.get("telefono")
        direccion = request.POST.get("direccion")
        password = request.POST.get("password")

        if User.objects.filter(username=username).exists():
            messages.error(request, "El usuario ya existe.")
        else:
            user = User.objects.create_user(
                username=username,
                email=email,
                first_name=first_name,
                last_name=last_name,
                password=password,
                rol="OWNER",
            )
            Propietario.objects.create(
                user=user, telefono=telefono, direccion=direccion
            )
            messages.success(request, "Propietario creado correctamente ✅")
            return redirect("dashboard")

    return render(request, "core/crear_propietario_admin.html")


@login_required
def crear_mascota_admin(request):
    if request.user.rol != "ADMIN":
        messages.error(request, "No tienes permiso para esta acción.")
        return redirect("dashboard")

    propietarios = Propietario.objects.all()

    if request.method == "POST":
        has_error = False
        nombre = request.POST.get("nombre")
        especie = request.POST.get("especie")
        raza = request.POST.get("raza")
        sexo = request.POST.get("sexo")
        fecha_nacimiento = request.POST.get("fecha_nacimiento")
        propietario_id = request.POST.get("propietario")

        propietario = None
        if propietario_id:
            propietario = Propietario.objects.filter(id=propietario_id).first()

        if propietario is None:
            messages.error(request, "Debes seleccionar un propietario válido.")
            has_error = True

        fecha_obj = None
        if fecha_nacimiento:
            try:
                fecha_obj = datetime.strptime(fecha_nacimiento, "%Y-%m-%d").date()
            except ValueError:
                messages.error(request, "La fecha de nacimiento no es válida.")
                has_error = True
        else:
            messages.error(request, "Debes indicar la fecha de nacimiento.")
            has_error = True

        if not has_error:
            Paciente.objects.create(
                nombre=nombre,
                especie=especie,
                raza=raza,
                sexo=sexo,
                fecha_nacimiento=fecha_obj,
                propietario=propietario,
            )
            messages.success(request, "Mascota creada correctamente ✅")
            return redirect("dashboard")

    return render(
        request,
        "core/crear_mascota_admin.html",
        {"propietarios": propietarios},
    )


@login_required
def buscar_propietarios(request):
    if request.user.rol not in {"ADMIN", "ADMIN_OP"}:
        messages.error(request, "No tienes permiso para esta acción.")
        return redirect("dashboard")

    q = request.GET.get("q", "")
    resultados = []

    if q:
        resultados = Propietario.objects.filter(
            Q(user__first_name__icontains=q)
            | Q(user__last_name__icontains=q)
            | Q(user__username__icontains=q)
            | Q(telefono__icontains=q)
            | Q(direccion__icontains=q)
        )

    return render(
        request,
        "core/buscar_propietarios.html",
        {"resultados": resultados, "query": q},
    )


@login_required
def detalle_propietario(request, propietario_id):
    if request.user.rol not in {"ADMIN", "ADMIN_OP"}:
        messages.error(request, "No tienes permiso para esta acción.")
        return redirect("dashboard")

    propietario = get_object_or_404(Propietario, id=propietario_id)
    mascotas = Paciente.objects.filter(propietario=propietario)
    citas = Cita.objects.filter(paciente__in=mascotas)
    citas_pendientes = citas.filter(estado="programada")
    informes = HistorialMedico.objects.filter(paciente__in=mascotas)

    return render(
        request,
        "core/detalle_propietario.html",
        {
            "propietario": propietario,
            "mascotas": mascotas,
            "citas": citas,
            "citas_pendientes": citas_pendientes,
            "informes": informes,
        },
    )


@login_required
def gestionar_veterinarios(request):
    if request.user.rol != "ADMIN":
        messages.error(request, "No tienes permiso para gestionar veterinarios.")
        return redirect("dashboard")

    usuarios_no_vet = User.objects.exclude(rol="VET")

    if request.method == "POST":
        user_id = request.POST.get("usuario")
        usuario = get_object_or_404(User, id=user_id)
        usuario.rol = "VET"
        usuario.save(update_fields=["rol"])
        messages.success(request, f"{usuario.get_full_name()} ahora es Veterinario ✅")
        return redirect("gestionar_veterinarios")

    return render(
        request,
        "core/asignar_veterinario_admin.html",
        {"usuarios": usuarios_no_vet},
    )


@login_required
def dashboard_veterinarios(request):
    if request.user.rol != "ADMIN":
        return redirect("dashboard")

    veterinarios = User.objects.filter(rol="VET")

    vet_stats = []
    for vet in veterinarios:
        citas_totales = Cita.objects.filter(veterinario=vet).count()
        citas_programadas = Cita.objects.filter(
            veterinario=vet, estado="programada"
        ).count()
        citas_atendidas = Cita.objects.filter(
            veterinario=vet, estado="atendida"
        ).count()
        proximas_citas = Cita.objects.filter(
            veterinario=vet, estado="programada"
        ).order_by("fecha_hora")[:5]

        vet_stats.append(
            {
                "veterinario": vet,
                "citas_totales": citas_totales,
                "citas_pendientes": citas_programadas,
                "citas_atendidas": citas_atendidas,
                "proximas_citas": proximas_citas,
            }
        )

    return render(request, "core/dashboard_veterinarios.html", {"vet_stats": vet_stats})


@login_required
def historial_medico_vet(request):
    if request.user.rol not in {"ADMIN", "VET"}:
        messages.error(request, "No tienes permiso para acceder a esta sección.")
        return redirect("dashboard")

    query = request.GET.get("q", "")
    fecha_desde = request.GET.get("desde", "")
    fecha_hasta = request.GET.get("hasta", "")

    historiales = HistorialMedico.objects.all()

    if query:
        historiales = historiales.filter(
            Q(paciente__nombre__icontains=query)
            | Q(paciente__propietario__user__first_name__icontains=query)
            | Q(paciente__propietario__user__last_name__icontains=query)
            | Q(diagnostico__icontains=query)
        )

    if fecha_desde:
        historiales = historiales.filter(fecha__gte=fecha_desde)
    if fecha_hasta:
        historiales = historiales.filter(fecha__lte=fecha_hasta)

    return render(
        request,
        "core/historial_medico_vet.html",
        {
            "historiales": historiales,
            "query": query,
            "fecha_desde": fecha_desde,
            "fecha_hasta": fecha_hasta,
        },
    )


@login_required
def detalle_historial(request, historial_id):
    historial = get_object_or_404(HistorialMedico, id=historial_id)
    return render(request, "core/detalle_historial.html", {"historial": historial})


# ----------------------------
# Gestión de productos
# ----------------------------


@login_required
def admin_productos_list(request):
    if request.user.rol != "ADMIN":
        messages.error(request, "No tienes permiso para gestionar la tienda.")
        return redirect("dashboard")

    if not _producto_table_available():
        messages.warning(
            request,
            "La tienda aún no está lista. Ejecuta las migraciones para crear la tabla de productos.",
        )
        productos = Producto.objects.none()
    else:
        productos = Producto.objects.all().order_by("-actualizado")
    return render(request, "core/admin_productos_list.html", {"productos": productos})


@login_required
def admin_producto_crear(request):
    if request.user.rol != "ADMIN":
        messages.error(request, "No tienes permiso para gestionar la tienda.")
        return redirect("dashboard")

    if not _producto_table_available():
        messages.error(
            request,
            "Debes ejecutar las migraciones antes de crear productos en la tienda.",
        )
        return redirect("admin_productos_list")

    if request.method == "POST":
        form = ProductoForm(request.POST, request.FILES)
        if form.is_valid():
            producto = form.save()
            messages.success(
                request, f"Producto {producto.nombre} creado correctamente ✅"
            )
            return redirect("admin_productos_list")
    else:
        form = ProductoForm()

    return render(
        request,
        "core/admin_producto_form.html",
        {"form": form, "titulo": "Nuevo producto"},
    )


@login_required
def admin_producto_editar(request, producto_id):
    if request.user.rol != "ADMIN":
        messages.error(request, "No tienes permiso para gestionar la tienda.")
        return redirect("dashboard")

    if not _producto_table_available():
        messages.error(
            request,
            "Debes ejecutar las migraciones antes de editar productos en la tienda.",
        )
        return redirect("admin_productos_list")

    producto = get_object_or_404(Producto, id=producto_id)

    if request.method == "POST":
        form = ProductoForm(request.POST, request.FILES, instance=producto)
        if form.is_valid():
            form.save()
            messages.success(
                request, f"Producto {producto.nombre} actualizado correctamente ✅"
            )
            return redirect("admin_productos_list")
    else:
        form = ProductoForm(instance=producto)

    return render(
        request,
        "core/admin_producto_form.html",
        {"form": form, "titulo": f"Editar {producto.nombre}", "producto": producto},
    )


# ----------------------------
# Autenticación
# ----------------------------

def login_view(request):
    if request.method == "POST":
        username = request.POST.get("username")
        password = request.POST.get("password")
        user = authenticate(request, username=username, password=password)
        if user is not None:
            login(request, user)
            return redirect("dashboard")
        messages.error(request, "Usuario o contraseña incorrectos.")
    return render(request, "core/login.html")


def logout_view(request):
    logout(request)
    return redirect("login")


def registro_propietario(request):
    if request.method == "POST":
        username = request.POST.get("username")
        email = request.POST.get("email")
        first_name = request.POST.get("first_name")
        last_name = request.POST.get("last_name")
        telefono = request.POST.get("telefono")
        password1 = request.POST.get("password1")
        password2 = request.POST.get("password2")

        if password1 != password2:
            messages.error(request, "Las contraseñas no coinciden.")
        elif User.objects.filter(username=username).exists():
            messages.error(request, "El usuario ya existe.")
        else:
            user = User.objects.create_user(
                username=username,
                email=email,
                first_name=first_name,
                last_name=last_name,
                password=password1,
                rol="OWNER",
            )
            Propietario.objects.create(user=user, telefono=telefono)
            messages.success(
                request, "Registro exitoso. Ahora puedes iniciar sesión."
            )
            return redirect("login")

    return render(request, "core/registro_propietario.html")
