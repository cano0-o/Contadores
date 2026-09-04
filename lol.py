from flask import Flask, request, jsonify, render_template_string
import requests
import re
from bs4 import BeautifulSoup

app = Flask(__name__)

# ==========================================
# 1. EL FRONTEND (HTML + CSS + JS) EN UNA VARIABLE
# ==========================================
HTML_TEMPLATE = """
<!DOCTYPE html>
<html lang="es">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <meta name="theme-color" content="#0d6efd">
  <title>WebMonitor — Contadores Ricoh</title>
  <!-- Bootstrap 5.3 -->
  <link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.3/dist/css/bootstrap.min.css" rel="stylesheet">
  <link href="https://cdn.jsdelivr.net/npm/bootstrap-icons@1.11.3/font/bootstrap-icons.min.css" rel="stylesheet">
  <style>
    :root {
      --wm-primary: #0d6efd;
      --wm-dark: #0b1220;
      --wm-card: rgba(255,255,255,.92);
    }
    body {
      min-height: 100vh;
      background: radial-gradient(circle at 10% 10%, rgba(13,110,253,.14), transparent 30%),
                  radial-gradient(circle at 90% 90%, rgba(111,66,193,.12), transparent 30%),
                  #f4f7fb;
    }
    .navbar-brand { font-weight: 700; letter-spacing: -.02em; }
    .hero { padding: 4.5rem 0 2.5rem; }
    .hero-icon {
      width: 64px; height: 64px; display: inline-flex; align-items: center;
      justify-content: center; border-radius: 18px; color: #fff;
      background: linear-gradient(135deg, #0d6efd, #6f42c1);
      box-shadow: 0 12px 30px rgba(13,110,253,.25); font-size: 1.7rem;
    }
    .machine-card {
      border: 0; border-radius: 20px; background: var(--wm-card);
      box-shadow: 0 12px 35px rgba(20, 32, 55, .08);
      transition: transform .2s ease, box-shadow .2s ease; overflow: hidden;
    }
    .machine-card:hover { transform: translateY(-3px); box-shadow: 0 18px 45px rgba(20, 32, 55, .13); }
    .machine-status {
      width: 10px; height: 10px; display: inline-block; border-radius: 50%;
      background: #20c997; box-shadow: 0 0 0 4px rgba(32,201,151,.12);
    }
    .ip-badge { font-family: monospace; font-size: .82rem; }
    .search-box { max-width: 480px; }
  </style>
</head>
<body>

<nav class="navbar bg-white border-bottom sticky-top">
  <div class="container py-2">
    <a class="navbar-brand d-flex align-items-center gap-2" href="#">
      <i class="bi bi-printer-fill text-primary"></i> Contadores
    </a>
    <span class="badge text-bg-light border">cano</span>
  </div>
</nav>

<main class="container">
  <section class="hero text-center">
    <div class="hero-icon mb-3"><i class="bi bi-speedometer2"></i></div>
    <h1 class="fw-bold mb-2">Consulta de contadores - Sucursal México</h1>
    <p class="text-secondary mb-4">Accede directamente al contador de cada equipo.</p>
    <div class="input-group search-box mx-auto shadow-sm">
      <span class="input-group-text bg-white border-end-0"><i class="bi bi-search text-secondary"></i></span>
      <input id="searchInput" type="search" class="form-control border-start-0" placeholder="Buscar por modelo o IP..." autocomplete="off">
    </div>
  </section>

  <section class="pb-5">
    <div class="row g-4" id="machineList">

      <!-- EQUIPO 1 -->
      <div class="col-12 col-md-6 col-lg-4 machine-item" data-search="ricoh aficio mp w3601 192.168.1.164">
        <article class="card machine-card h-100">
          <div class="card-body p-4">
            <div class="d-flex justify-content-between align-items-start mb-4">
              <div class="d-flex align-items-center gap-2">
                <span class="machine-status"></span><span class="small text-secondary">Equipo disponible</span>
              </div>
              <i class="bi bi-printer fs-4 text-primary"></i>
            </div>
            <h2 class="h5 fw-bold mb-1">Ricoh Aficio MP W3601</h2>
            <div class="text-secondary mb-4">
              <span class="badge bg-light text-dark border ip-badge">192.168.1.164</span>
            </div>
            <!-- Nuevo sistema de botón y contenedor de resultados -->
            <div class="counter-container">
              <button class="btn btn-primary w-100 btn-fetch-counter" data-ip="192.168.1.164">
                <i class="bi bi-cloud-download me-2"></i> Extraer contador
              </button>
              <div class="counter-result mt-3 d-none text-center bg-light rounded p-2 border">
                <span class="text-secondary small">Total Impresiones</span><br>
                <span class="fs-4 fw-bold text-dark total-number">...</span>
              </div>
            </div>
          </div>
        </article>
      </div>

      <!-- EQUIPO 2 -->
      <div class="col-12 col-md-6 col-lg-4 machine-item" data-search="ricoh mp 501 192.168.1.70">
        <article class="card machine-card h-100">
          <div class="card-body p-4">
            <div class="d-flex justify-content-between align-items-start mb-4">
              <div class="d-flex align-items-center gap-2">
                <span class="machine-status"></span><span class="small text-secondary">Equipo disponible</span>
              </div>
              <i class="bi bi-printer fs-4 text-primary"></i>
            </div>
            <h2 class="h5 fw-bold mb-1">Ricoh MP 501</h2>
            <div class="text-secondary mb-4">
              <span class="badge bg-light text-dark border ip-badge">192.168.1.70</span>
            </div>
            <div class="counter-container">
              <button class="btn btn-primary w-100 btn-fetch-counter" data-ip="192.168.1.70">
                <i class="bi bi-cloud-download me-2"></i> Extraer contador
              </button>
              <div class="counter-result mt-3 d-none text-center bg-light rounded p-2 border">
                <span class="text-secondary small">Total Impresiones</span><br>
                <span class="fs-4 fw-bold text-dark total-number">...</span>
              </div>
            </div>
          </div>
        </article>
      </div>
      
      <!-- Agrega más equipos copiando y pegando el bloque de arriba y cambiando el data-ip -->

    </div>

    <div id="noResults" class="text-center py-5 d-none">
      <i class="bi bi-search fs-1 text-secondary"></i>
      <h3 class="h5 mt-3">No se encontraron equipos</h3>
      <p class="text-secondary mb-0">Prueba con otro modelo o dirección IP.</p>
    </div>
  </section>
</main>

<script>
  // LÓGICA DE BÚSQUEDA
  const searchInput = document.getElementById('searchInput');
  const items = [...document.querySelectorAll('.machine-item')];
  const noResults = document.getElementById('noResults');

  searchInput.addEventListener('input', () => {
    const query = searchInput.value.trim().toLowerCase();
    let visible = 0;
    items.forEach(item => {
      const matches = item.dataset.search.includes(query);
      item.classList.toggle('d-none', !matches);
      if (matches) visible++;
    });
    noResults.classList.toggle('d-none', visible === 0);
  });

  // LÓGICA PARA EXTRAER CONTADORES
  const fetchButtons = document.querySelectorAll('.btn-fetch-counter');
  fetchButtons.forEach(button => {
    button.addEventListener('click', async (e) => {
      const btn = e.currentTarget;
      const ip = btn.getAttribute('data-ip');
      const container = btn.closest('.counter-container');
      const resultDiv = container.querySelector('.counter-result');
      const totalSpan = container.querySelector('.total-number');

      // Cambiar visualmente el botón
      const originalText = btn.innerHTML;
      btn.innerHTML = '<span class="spinner-border spinner-border-sm"></span> Consultando...';
      btn.disabled = true;

      try {
        // Al estar todo en el mismo programa, usamos ruta relativa (/api/contador)
        const response = await fetch(`/api/contador?ip=${ip}`);
        const data = await response.json();

        if (data.success) {
          totalSpan.textContent = parseInt(data.total).toLocaleString('es-MX'); 
          btn.classList.add('d-none');
          resultDiv.classList.remove('d-none');
        } else {
          alert("Error de impresora: " + data.error);
          btn.innerHTML = originalText;
          btn.disabled = false;
        }
      } catch (err) {
        alert("Error: No se pudo conectar al servidor local.");
        btn.innerHTML = originalText;
        btn.disabled = false;
      }
    });
  });
</script>
</body>
</html>
"""

# ==========================================
# 2. LAS RUTAS DEL SERVIDOR (EL BACKEND)
# ==========================================

# Ruta principal: Muestra la página web
@app.route('/')
def index():
    return render_template_string(HTML_TEMPLATE)

@app.route('/api/contador', methods=['GET'])
def get_contador():
    ip = request.args.get('ip')
    if not ip:
        return jsonify({'success': False, 'error': 'Falta la IP'}), 400

    url = f"http://{ip}/web/guest/es/websys/status/getUnificationCounter.cgi"
    
    try:
        # Aumentamos el tiempo de espera a 10 segundos
        response = requests.get(url, timeout=10)
        
        # Si la impresora responde, pero rechaza la conexión (ej. pide contraseña)
        if response.status_code != 200:
            return jsonify({'success': False, 'error': f'Conectó, pero dio el error: {response.status_code}'})

        # Limpieza y extracción
        soup = BeautifulSoup(response.text, 'html.parser')
        texto_limpio = re.sub(r'\s+', '', soup.get_text())

        # Búsqueda del patrón "Total:123456"
        match = re.search(r'Total:(\d+)', texto_limpio)
        
        if match:
            total = match.group(1)
            return jsonify({'success': True, 'total': total})
        else:
            print("TEXTO RECIBIDO DE LA IMPRESORA:", texto_limpio) # Esto se imprimirá en tu consola negra
            return jsonify({'success': False, 'error': 'Página leída, pero no se encontró la palabra "Total:"'})
            
    except requests.exceptions.Timeout:
        return jsonify({'success': False, 'error': 'La impresora tardó demasiado en responder (Timeout).'})
    except requests.exceptions.ConnectionError:
        return jsonify({'success': False, 'error': f'No hay conexión. Verifica que la IP {ip} sea correcta.'})
    except Exception as e:
        return jsonify({'success': False, 'error': f'Error técnico: {str(e)}'})

# ==========================================
# 3. INICIO DEL PROGRAMA
# ==========================================
if __name__ == '__main__':
    # host='0.0.0.0' permite que otras computadoras en la red entren a la app
    app.run(host='0.0.0.0', port=5000, debug=True)