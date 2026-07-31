# ⚠️ ALERTA DE SEGURIDAD CRÍTICA

## 🚨 ROTACIÓN DE CLAVES REQUERIDA INMEDIATAMENTE

**Fecha de Detección:** $(date +%Y-%m-%d)

### Situación Detectada
Se detectaron claves privadas expuestas en el historial de Git del repositorio `planet-health`.

**Archivos comprometidos identificados:**
- `claves/planet_health_privada.pem`
- `claves/planet_health_publica.pem`

### Acción Requerida
Estas claves deben considerarse **COMPROMETIDAS** y deben ser rotadas (generadas de nuevo) inmediatamente en el proveedor de servicios correspondiente.

### Pasos a Seguir
1. **Generar nuevas claves** en el proveedor de servicios (AWS, Azure, GCP, etc.)
2. **Revocar las claves antiguas** inmediatamente
3. **Actualizar todas las configuraciones** que usaban las claves anteriores
4. **Notificar al equipo de seguridad** sobre la exposición
5. **Revisar logs de acceso** para detectar uso no autorizado

### Prevención Futura
El archivo `.gitignore` ha sido actualizado para prevenir futuras fugas de:
- Archivos `.pem`, `.key`, `.crt`
- Carpetas `claves/` y `secrets/`
- Archivos `.env` y `.env.local`

---
**Nota:** Este archivo debe mantenerse en el repositorio como registro de la incidencia de seguridad.
