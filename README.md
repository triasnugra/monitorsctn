# Monitor SCTN · SCTNv2 · SCTNv3

Dashboard untuk memantau & mengevaluasi forward test tiga mesin scalping crypto. Dideploy otomatis ke GitHub Pages lewat GitHub Actions (tanpa server, tanpa dependensi pip).

## Cara pasang (semua lewat web GitHub)
1. Buat repo baru, mis. `monitorsctn`, lalu upload **seluruh isi** zip ini (termasuk folder `.github/`).
2. **Settings → Pages → Source: GitHub Actions**.
3. **Actions → monitor-sctn → Run workflow** (pertama kali). Selanjutnya jalan otomatis tiap 30 menit (menit 20 & 50).
4. Dashboard: `https://<username>.github.io/monitorsctn/`

## Konfigurasi
Edit `config/engines.json`: `owner`, nama repo tiap mesin, dan gate validasi (150 trade, 20 minggu, PF ≥ 1,2).
Bila repo mesin **privat**, buat secret `ENGINES_TOKEN` (fine-grained PAT, read-only: Contents + Actions untuk ketiga repo).

## Yang dipantau
- Status gate per mesin (UNVERIFIABLE / PASS / FAIL), progres trade & minggu
- Win rate, Avg R, Total R, PF, SQN, CI95 (t & bootstrap), max drawdown, loss streak, fill rate, durasi hold
- Kurva ekuitas forward, breakdown per trigger / arah / skor / simbol, posisi aktif, trade & sinyal terakhir
- Backtest vs forward (deteksi overfitting), hash config, universe scanner (v3)
- Kesehatan mesin (run workflow terakhir, umur data) dan tumpang tindih sinyal antar mesin

## Uji lokal (opsional)
`python scripts/collect.py --local-root <folder berisi sctn-main/ ...> --site site`
