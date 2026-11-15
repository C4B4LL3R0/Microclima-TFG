/* ==========================================================================
   ARCHIVO: static/js/app.js
   PROYECTO: Monitor de Microclima Edge para Conservación Preventiva
   FUNCIÓN: Cliente Socket.IO, renderizado en tiempo real y panel de pruebas.

   Este archivo NO calcula alarmas: recibe del servidor el estado ya evaluado
   (perfil, lectura, decisión acústica y estado del simulador). Se limita a
   pintar tarjetas, sincronizar controles y representar visualmente el patrón
   que el hilo del buzzer está reproduciendo.

   Seguridad: todos los textos procedentes del servidor se insertan con
   textContent (nunca innerHTML con datos externos), evitando XSS incluso en una
   red local.
   ========================================================================== */
(() => {
  "use strict";

  // ---------------------------------------------------------------------------
  // 1. Referencias al DOM. Se consultan una sola vez para evitar búsquedas
  //    repetidas en cada actualización (la web recibe un estado por segundo).
  // ---------------------------------------------------------------------------
  const $ = (selector, scope = document) => scope.querySelector(selector);
  const $$ = (selector, scope = document) => Array.from(scope.querySelectorAll(selector));

  const connectionPill = $("#connection-pill");
  const connectionText = $("#connection-text");
  const profileSelect = $("#profile-select");
  const profileDescription = $("#profile-description");
  const muteButton = $("#mute-button");
  const muteLabel = $("#mute-label");
  const muteHint = $("#mute-hint");

  const alarmPanel = $("#alarm-panel");
  const alarmTitle = $("#alarm-title");
  const alarmDescription = $("#alarm-description");
  const alarmReasons = $("#alarm-reasons");
  const patternCode = $("#pattern-code");
  const patternBars = $("#pattern-bars");
  const patternLabel = $("#pattern-label");

  const pressureValue = $("#pressure-value");
  const lastReading = $("#last-reading");
  const sensorSource = $("#sensor-source");
  const buzzerStatus = $("#buzzer-status");
  const healthChip = $("#health-chip");
  const healthSummary = $("#health-summary");
  const sensorErrors = $("#sensor-errors");
  const hardwarePlatform = $("#hardware-platform");
  const hardwareRtc = $("#hardware-rtc");
  const hardwareBuzzer = $("#hardware-buzzer");
  const serverClock = $("#server-clock");
  const toast = $("#toast");

  const simPanel = $("#sim-panel");
  const simToggle = $("#sim-manual-toggle");
  const simModeLabel = $("#sim-mode-label");
  const simControlKeys = ["temperature", "humidity", "eco2", "tvoc"];
  const simElements = {};

  simControlKeys.forEach((key) => {
    simElements[key] = {
      control: $(`.sim-control[data-sim-key="${key}"]`),
      range: $(`#sim-${key}-range`),
      number: $(`#sim-${key}-number`),
    };
  });

  // ---------------------------------------------------------------------------
  // 2. Estado local del cliente.
  //    appState es la última instantánea recibida. No se inventan valores.
  // ---------------------------------------------------------------------------
  let appState = null;
  let profilesSignature = "";
  let patternSignature = "";
  let animationToken = 0;
  let animationTimers = [];
  let toastTimer = null;
  let simDirtyUntil = 0;
  const simDebounceTimers = {};

  // ---------------------------------------------------------------------------
  // 3. Utilidades de formato y avisos.
  // ---------------------------------------------------------------------------
  function numberText(value, digits) {
    if (value === null || value === undefined || Number.isNaN(Number(value))) {
      return "—";
    }
    try {
      return Number(value).toLocaleString("es-ES", {
        minimumFractionDigits: digits,
        maximumFractionDigits: digits,
      });
    } catch (_error) {
      return Number(value).toFixed(digits);
    }
  }

  function dateTimeText(isoString) {
    if (!isoString) return "—";
    const date = new Date(isoString);
    if (Number.isNaN(date.getTime())) return isoString;
    try {
      return date.toLocaleString("es-ES", {
        day: "2-digit",
        month: "2-digit",
        year: "numeric",
        hour: "2-digit",
        minute: "2-digit",
        second: "2-digit",
      });
    } catch (_error) {
      return date.toLocaleString();
    }
  }

  function showToast(message, isError = false) {
    if (!toast) return;
    toast.textContent = message;
    toast.classList.toggle("is-error", Boolean(isError));
    toast.hidden = false;
    window.clearTimeout(toastTimer);
    toastTimer = window.setTimeout(() => {
      toast.hidden = true;
    }, 5500);
  }

  function setConnection(kind, text) {
    if (!connectionPill) return;
    connectionPill.classList.remove("is-online", "is-offline", "is-waiting");
    connectionPill.classList.add(`is-${kind}`);
    if (connectionText) connectionText.textContent = text;
    const connected = kind === "online";
    if (profileSelect) profileSelect.disabled = !connected || !appState;
    if (muteButton) muteButton.disabled = !connected || !appState;
    if (simToggle) simToggle.disabled = !connected || !appState || !appState.sim;
  }

  function rangeText(limit) {
    if (!limit) return "—";
    if (limit.min === null || limit.min === undefined) {
      return `≤ ${numberText(limit.max, 0)} ${limit.unit}`;
    }
    return `${numberText(limit.min, 1)} – ${numberText(limit.max, 1)} ${limit.unit}`;
  }

  function statusText(status) {
    if (status === "normal") return "Normal";
    if (status === "high" || status === "low") return "Alerta";
    return "Sin datos";
  }

  // ---------------------------------------------------------------------------
  // 4. Perfil normativo. Se reconstruye el <select> solo si el catálogo cambia;
  //    así no se cierra el desplegable cada segundo. El cambio de un cliente se
  //    propaga a todos mediante profile_changed.
  // ---------------------------------------------------------------------------
  function populateProfiles(profiles) {
    if (!profileSelect || !Array.isArray(profiles)) return;
    const signature = JSON.stringify(
      profiles.map((profile) => [profile.id, profile.name, profile.limits])
    );
    if (signature !== profilesSignature) {
      profilesSignature = signature;
      profileSelect.replaceChildren();
      profiles.forEach((profile) => {
        const option = document.createElement("option");
        option.value = profile.id;
        option.textContent = profile.name;
        profileSelect.appendChild(option);
      });
    }
    profileSelect.disabled = false;
  }

  function renderProfileControls(state) {
    if (!state || !state.profile) return;
    populateProfiles(state.profiles);
    if (profileSelect && profileSelect.value !== state.selected_profile) {
      profileSelect.value = state.selected_profile;
    }
    if (profileDescription) {
      profileDescription.textContent = state.profile.description
        ? `${state.profile.description} Límites aplicados por el servidor.`
        : "Los límites se aplican a las tarjetas y a la alarma acústica.";
    }
  }

  // ---------------------------------------------------------------------------
  // 5. Tarjetas de sensores. La decisión "normal/alerta" viene del backend en
  //    alarm.statuses; el cliente solo traduce el estado a estilos.
  // ---------------------------------------------------------------------------
  function renderSensorCards(state) {
    const reading = state.reading || {};
    const alarm = state.alarm || {};
    const statuses = alarm.statuses || {};
    const limits = (state.profile && state.profile.limits) || {};

    $$(".sensor-card").forEach((card) => {
      const key = card.dataset.sensor;
      const limit = limits[key];
      const value = reading[key];
      const status = statuses[key] || "unknown";
      const isAlert = status === "high" || status === "low";
      card.dataset.status = isAlert ? "alert" : (status === "normal" ? "normal" : "unknown");

      const badge = $(".status-badge", card);
      if (badge) badge.textContent = statusText(status);

      const valueElement = $(".sensor-value", card);
      if (valueElement) {
        const digits = key === "temperature" || key === "humidity" ? 2 : 0;
        valueElement.textContent = numberText(value, digits);
      }

      const unitElement = $(".sensor-unit", card);
      if (unitElement && limit) unitElement.textContent = limit.unit;

      const rangeElement = $(".sensor-range strong", card);
      if (rangeElement) rangeElement.textContent = rangeText(limit);
    });
  }

  // ---------------------------------------------------------------------------
  // 6. Patrón acústico. Se generan barras proporcionales a la duración y se
  //    activan con temporizadores para reflejar el ritmo real. No produce audio:
  //    el sonido corresponde al buzzer del servidor (GPIO o consola).
  // ---------------------------------------------------------------------------
  function stopPatternAnimation() {
    animationToken += 1;
    animationTimers.forEach((timer) => window.clearTimeout(timer));
    animationTimers = [];
    if (patternBars) {
      $$(".pattern-beep", patternBars).forEach((bar) => bar.classList.remove("is-active"));
    }
  }

  function startPatternAnimation(pattern) {
    if (!patternBars || !Array.isArray(pattern.beeps_ms) || pattern.beeps_ms.length === 0) {
      return;
    }
    const token = animationToken;
    const bars = $$(".pattern-beep", patternBars);

    const cycle = () => {
      if (token !== animationToken) return;
      let offset = 0;
      bars.forEach((bar, index) => {
        const duration = pattern.beeps_ms[index] || 100;
        animationTimers.push(window.setTimeout(() => {
          if (token === animationToken) bar.classList.add("is-active");
        }, offset));
        offset += duration;
        animationTimers.push(window.setTimeout(() => {
          if (token === animationToken) bar.classList.remove("is-active");
        }, offset));
        offset += pattern.gap_ms || 0;
      });
      animationTimers.push(
        window.setTimeout(cycle, offset + (pattern.repeat_delay_ms || 1000))
      );
    };
    cycle();
  }

  function renderPattern(pattern, muted, active) {
    if (!patternBars) return;
    const signature = `${pattern.id}:${muted}:${active}`;
    if (signature === patternSignature) return;
    patternSignature = signature;
    stopPatternAnimation();
    patternBars.replaceChildren();
    patternBars.classList.toggle("is-muted", Boolean(muted));

    if (patternCode) {
      patternCode.textContent = active && pattern.code ? pattern.code : "—";
    }
    if (patternLabel) {
      if (active) {
        patternLabel.textContent = muted
          ? "Patrón activo, pero silenciado en el buzzer."
          : `${pattern.description} Repetición automática.`;
      } else {
        patternLabel.textContent = "Sin patrón activo.";
      }
    }

    if (!active || !Array.isArray(pattern.beeps_ms)) return;
    pattern.beeps_ms.forEach((duration) => {
      const bar = document.createElement("span");
      let sizeClass = "beep-rapid";
      if (duration >= 500) sizeClass = "beep-long";
      else if (duration >= 150) sizeClass = "beep-short";
      bar.className = `pattern-beep ${sizeClass}`;
      bar.title = `${duration} ms`;
      patternBars.appendChild(bar);
    });

    if (!muted) startPatternAnimation(pattern);
  }

  function renderAlarm(state) {
    const alarm = state.alarm || {};
    const pattern = alarm.pattern || { id: "silent", beeps_ms: [], code: "" };
    const active = Boolean(alarm.active);
    const muted = Boolean(alarm.muted);
    let panelState = "normal";

    if (active && muted) panelState = "muted";
    else if (active) panelState = "alert";
    if (alarmPanel) alarmPanel.dataset.state = panelState;

    if (alarmTitle) {
      if (active && muted) alarmTitle.textContent = "Alarma activa · sonido silenciado";
      else if (active) alarmTitle.textContent = pattern.label;
      else alarmTitle.textContent = "Parámetros dentro de norma";
    }

    if (alarmDescription) {
      if (active) {
        const count = (alarm.reasons || []).length;
        const noun = count === 1 ? "variable" : "variables";
        alarmDescription.textContent = muted
          ? `${count} ${noun} fuera del perfil. El patrón ${pattern.code} queda retenido hasta reactivar el sonido.`
          : `${count} ${noun} fuera del perfil. El buzzer reproduce el patrón ${pattern.code}.`;
      } else {
        alarmDescription.textContent = state.profile
          ? `Perfil ${state.profile.name}. Temperatura, humedad y calidad de aire dentro de los límites configurados.`
          : "Sin variables fuera de rango.";
      }
    }

    if (alarmReasons) {
      alarmReasons.replaceChildren();
      (alarm.reasons || []).forEach((reason) => {
        const item = document.createElement("li");
        item.textContent = reason;
        alarmReasons.appendChild(item);
      });
      alarmReasons.hidden = !(alarm.reasons && alarm.reasons.length);
    }

    renderPattern(pattern, muted, active);

    if (muteButton) {
      muteButton.classList.toggle("is-muted", muted);
      muteButton.setAttribute("aria-pressed", muted ? "true" : "false");
      muteButton.disabled = !socket.connected;
    }
    if (muteLabel) muteLabel.textContent = muted ? "Reactivar alarma" : "Silenciar alarma";
    if (muteHint) {
      muteHint.textContent = muted
        ? "Pitidos silenciados en todos los dispositivos."
        : "Sincronizado con todos los dispositivos.";
    }
  }

  // ---------------------------------------------------------------------------
  // 7. Panel de simulación. Solo existe en microclima_sim. Los valores llegan
  //    del servidor; durante 700 ms tras una interacción no se sobrescriben los
  //    controles locales para evitar saltos mientras el ACK está en tránsito.
  // ---------------------------------------------------------------------------
  function configureSimRanges(ranges) {
    if (!ranges) return;
    simControlKeys.forEach((key) => {
      const elements = simElements[key];
      const limits = ranges[key];
      if (!elements || !limits) return;
      [elements.range, elements.number].forEach((input) => {
        if (!input) return;
        input.min = String(limits.min);
        input.max = String(limits.max);
        input.step = String(limits.step);
      });
      const extents = $$(".range-extents span", elements.control || document);
      if (extents.length >= 2) {
        extents[0].textContent = `${numberText(limits.min, 0)} ${limits.unit}`;
        extents[1].textContent = `${numberText(limits.max, 0)} ${limits.unit}`;
      }
    });
  }

  function setSimInputsDisabled(disabled) {
    simControlKeys.forEach((key) => {
      const elements = simElements[key];
      if (!elements) return;
      if (elements.range) elements.range.disabled = disabled;
      if (elements.number) elements.number.disabled = disabled;
    });
  }

  function setSimValues(values, force = false) {
    if (!values) return;
    if (!force && Date.now() < simDirtyUntil) return;
    simControlKeys.forEach((key) => {
      const elements = simElements[key];
      const value = values[key];
      if (!elements || value === null || value === undefined) return;
      if (elements.range) elements.range.value = String(value);
      if (elements.number) elements.number.value = String(value);
    });
  }

  function renderSimulation(state) {
    if (!simPanel || !simToggle) return;
    const sim = state.sim;
    if (!sim) {
      simPanel.hidden = true;
      return;
    }
    simPanel.hidden = false;
    configureSimRanges(sim.ranges);
    const manual = sim.mode === "manual";
    simToggle.checked = manual;
    simToggle.setAttribute("aria-checked", manual ? "true" : "false");
    simToggle.disabled = !socket.connected;
    if (simModeLabel) simModeLabel.textContent = manual ? "Manual" : "Automático";
    setSimInputsDisabled(!manual);
    setSimValues(sim.values);
  }

  function emitSimUpdate(payload, onDone) {
    if (!socket.connected) {
      showToast("Sin conexión con el servidor de simulación.", true);
      return;
    }
    socket.timeout(3500).emit("sim_control", payload, (error, response) => {
      if (error) {
        showToast("El servidor no respondió a la inyección de datos.", true);
        simDirtyUntil = 0;
        if (onDone) onDone(false);
        return;
      }
      if (!response || !response.ok) {
        showToast((response && response.error) || "Valor de simulación rechazado.", true);
        simDirtyUntil = 0;
        if (onDone) onDone(false);
        return;
      }
      if (onDone) onDone(true);
    });
  }

  function currentSimValues() {
    const values = {};
    simControlKeys.forEach((key) => {
      const elements = simElements[key];
      const parsed = Number(elements && elements.number ? elements.number.value : NaN);
      if (Number.isFinite(parsed)) values[key] = parsed;
    });
    return values;
  }

  function bindSimulationControls() {
    if (!simPanel) return;
    simControlKeys.forEach((key) => {
      const elements = simElements[key];
      if (!elements) return;

      const schedule = (value) => {
        window.clearTimeout(simDebounceTimers[key]);
        simDebounceTimers[key] = window.setTimeout(() => {
          emitSimUpdate({ mode: "manual", [key]: value });
        }, 120);
      };

      if (elements.range) {
        elements.range.addEventListener("input", () => {
          const value = Number(elements.range.value);
          if (elements.number) elements.number.value = String(value);
          simDirtyUntil = Date.now() + 700;
          schedule(value);
        });
      }

      if (elements.number) {
        elements.number.addEventListener("input", () => {
          const raw = elements.number.value.trim();
          if (raw === "") return;
          const value = Number(raw);
          if (!Number.isFinite(value)) return;
          if (elements.range) elements.range.value = String(value);
          simDirtyUntil = Date.now() + 700;
          schedule(value);
        });
      }
    });

    if (simToggle) {
      simToggle.addEventListener("change", () => {
        const manual = simToggle.checked;
        const payload = manual
          ? { mode: "manual", ...currentSimValues() }
          : { mode: "auto" };
        simDirtyUntil = Date.now() + 1000;
        simToggle.disabled = true;
        emitSimUpdate(payload, (ok) => {
          simToggle.disabled = !socket.connected;
          if (!ok) {
            simToggle.checked = !manual;
            simToggle.setAttribute("aria-checked", manual ? "false" : "true");
            simDirtyUntil = 0;
          }
        });
      });
    }
  }

  // ---------------------------------------------------------------------------
  // 8. Diagnóstico: salud de sensores, RTC, buzzer y reloj del servidor.
  // ---------------------------------------------------------------------------
  function renderSystem(state) {
    const reading = state.reading || {};
    const alarm = state.alarm || {};
    const system = state.system || {};
    const hardware = system.hardware || {};
    const errors = Array.isArray(reading.errors) ? reading.errors : [];

    if (pressureValue) pressureValue.textContent = `${numberText(reading.pressure, 2)} hPa`;
    if (lastReading) lastReading.textContent = dateTimeText(reading.timestamp);
    if (sensorSource) sensorSource.textContent = reading.source || "—";
    if (serverClock) serverClock.textContent = `Hora del servidor: ${dateTimeText(state.server_time)}`;

    if (buzzerStatus) {
      if (!alarm.available) buzzerStatus.textContent = "No disponible";
      else if (alarm.muted) buzzerStatus.textContent = "Silenciado";
      else if (alarm.active) buzzerStatus.textContent = "Alarma activa";
      else buzzerStatus.textContent = "En espera";
    }

    if (sensorErrors) {
      sensorErrors.replaceChildren();
      errors.forEach((message) => {
        const item = document.createElement("li");
        item.textContent = message;
        sensorErrors.appendChild(item);
      });
      sensorErrors.hidden = errors.length === 0;
    }

    if (healthChip) {
      let chipState = "ok";
      let chipText = "Operativo";
      if (errors.length > 0) {
        chipState = "warning";
        chipText = "Incidencia";
      } else if (reading.warmup) {
        chipState = "warning";
        chipText = "Calibrando";
      } else if (
        reading.temperature === null || reading.temperature === undefined
      ) {
        chipState = "waiting";
        chipText = "Sin datos";
      }
      healthChip.dataset.state = chipState;
      healthChip.textContent = chipText;
    }

    if (healthSummary) {
      if (errors.length > 0) {
        healthSummary.textContent =
          `Se han detectado ${errors.length} incidencia(s). Revise el bus I2C y los conectores; la web sigue activa.`;
      } else if (reading.warmup) {
        healthSummary.textContent =
          "SGP30 en calentamiento inicial (15 s). Las medidas de gases son preliminares.";
      } else if (state.mode === "sim") {
        healthSummary.textContent =
          "Generador de simulación activo. No hay hardware I2C ni GPIO implicados.";
      } else {
        healthSummary.textContent =
          "BME280 y SGP30 responden correctamente. La última lectura se ha validado sin errores.";
      }
    }

    if (hardwarePlatform) {
      hardwarePlatform.textContent = hardware.platform
        ? `Plataforma: ${hardware.platform}`
        : "Plataforma no disponible.";
    }
    if (hardwareRtc) {
      hardwareRtc.textContent = hardware.rtc ? `RTC: ${hardware.rtc}` : "RTC: sin información.";
    }
    if (hardwareBuzzer) {
      hardwareBuzzer.textContent = hardware.buzzer
        ? `Buzzer: ${hardware.buzzer}`
        : "Buzzer: sin información.";
    }
  }

  // ---------------------------------------------------------------------------
  // 9. Punto de entrada de renderizado. Cada state_update repinta el estado
  //    completo; es idempotente y no reinicia animaciones si no ha cambiado el
  //    patrón acústico.
  // ---------------------------------------------------------------------------
  function applyState(state) {
    if (!state) return;
    appState = state;
    setConnection("online", "En línea");
    renderProfileControls(state);
    renderSensorCards(state);
    renderAlarm(state);
    renderSimulation(state);
    renderSystem(state);
  }

  // ---------------------------------------------------------------------------
  // 10. Eventos de Socket.IO. El servidor envía initial_state al conectar y
  //     state_update en cada muestra/cambio.
  // ---------------------------------------------------------------------------
  if (typeof window.io !== "function") {
    setConnection("offline", "Socket.IO no cargado");
    showToast("No se encontró socket.io.min.js. Compruebe static/js/.", true);
    return;
  }

  const socket = window.io({
    transports: ["polling", "websocket"],
    reconnection: true,
    reconnectionAttempts: Infinity,
    reconnectionDelay: 800,
    reconnectionDelayMax: 4000,
    timeout: 10000,
  });

  socket.on("connect", () => {
    setConnection("online", "En línea");
    // Se solicita explícitamente el estado además de initial_state. Si una
    // reconexión ocurre en un instante en que el servidor aún no está listo,
    // esta petición recupera la instantánea más reciente.
    socket.emit("request_state");
  });

  socket.on("disconnect", (reason) => {
    setConnection("offline", "Desconectado");
    if (profileSelect) profileSelect.disabled = true;
    if (muteButton) muteButton.disabled = true;
    if (simToggle) simToggle.disabled = true;
    if (reason !== "io client disconnect") {
      showToast("Conexión perdida. Reintentando con el servidor…", true);
    }
  });

  socket.on("connect_error", () => {
    setConnection("offline", "Reintentando…");
  });

  socket.on("reconnect_attempt", () => {
    setConnection("waiting", "Reconectando…");
  });

  socket.on("initial_state", applyState);
  socket.on("state_update", applyState);

  socket.on("profile_changed", (data) => {
    if (!appState || !data) return;
    appState.profiles = data.profiles || appState.profiles;
    appState.selected_profile = data.selected_profile;
    appState.profile = data.profile;
    renderProfileControls(appState);
    // El servidor emite state_update inmediatamente después; este render
    // temprano evita que el desplegable tarde un ciclo en otros dispositivos.
    renderSensorCards(appState);
    renderAlarm(appState);
  });

  socket.on("mute_changed", (data) => {
    if (!appState || !data) return;
    appState.alarm.muted = Boolean(data.muted);
    renderAlarm(appState);
    renderSystem(appState);
  });

  socket.on("sim_changed", (sim) => {
    if (!appState || !sim) return;
    appState.sim = sim;
    renderSimulation(appState);
  });

  socket.on("server_error", (data) => {
    showToast((data && data.message) || "Operación rechazada por el servidor.", true);
  });

  // ---------------------------------------------------------------------------
  // 11. Controles de perfil y silencio con ACK y timeout. Si el servidor no
  //     responde, se revierte la interfaz y se avisa al operador.
  // ---------------------------------------------------------------------------
  if (profileSelect) {
    profileSelect.addEventListener("change", () => {
      if (!appState || !socket.connected) return;
      const requested = profileSelect.value;
      profileSelect.disabled = true;
      socket.timeout(5000).emit(
        "select_profile",
        { profile_id: requested },
        (error, response) => {
          profileSelect.disabled = !socket.connected;
          if (error || !response || !response.ok) {
            showToast(
              (response && response.error) ||
                "No se pudo cambiar el perfil. Se conserva el anterior.",
              true
            );
            profileSelect.value = appState.selected_profile;
            return;
          }
          // El estado definitivo llega por profile_changed/state_update.
          showToast(`Perfil aplicado: ${requested}`);
        }
      );
    });
  }

  if (muteButton) {
    muteButton.addEventListener("click", () => {
      if (!appState || !socket.connected) return;
      const desired = !appState.alarm.muted;
      muteButton.disabled = true;
      socket.timeout(5000).emit("set_mute", { muted: desired }, (error, response) => {
        muteButton.disabled = !socket.connected;
        if (error || !response || !response.ok) {
          showToast(
            (response && response.error) || "No se pudo cambiar el estado del sonido.",
            true
          );
          return;
        }
        showToast(desired ? "Alarma silenciada." : "Alarma sonora reactivada.");
      });
    });
  }

  bindSimulationControls();
})();
