# clubbot

Réserve automatiquement les cours favoris sur members.clubconnect.fr, pour plusieurs comptes d'un même club, le jour de l'ouverture (J+14 à 8h00, heure de Paris).

## Fonctionnement d'un passage (cron à 07:50 Paris)

1. Calcule le jour visé : aujourd'hui + 14. Si aucun compte ne veut de cours ce jour-là, le passage s'arrête sans aucune requête.
2. Connecte le premier compte et lit le planning de ce jour, une seule fois pour tous les comptes.
3. Retrouve chaque cours voulu (jour, heure de Paris, nom) :
   - cours absent du planning (jour férié…) : noté dans le journal, ignoré ;
   - cours `NOT_BOOKABLE` : noté, ignoré ;
   - nom absent et plusieurs cours à la même heure : refusé, il faut préciser le nom.
4. Connecte les autres comptes, avec une pause entre chaque, et lit leurs réservations pour ne jamais réserver deux fois.
5. Attend l'heure `bookable.notBefore` fournie par le site, plus 1 s, puis envoie une réservation par cours et par compte.
6. Si la réservation est refusée et que le cours est complet, inscrit le compte en liste d'attente.
7. Relit les réservations pour confirmer, puis envoie un résumé (webhook ou journal).

Pour un compte et un cours, cela fait 5 requêtes par jour.

## Garde-fous anti-blacklist

- **Espacement global** : au moins 1,5 s entre deux requêtes, tous comptes confondus (même IP).
- **Plafond** de 30 requêtes par passage.
- **Coupe-circuit** au premier 429, au premier 403 ou à une page HTML ou non JSON :
  - arrêt immédiat de tout le passage ;
  - écriture de `state/blocked.json` ;
  - plus aucune requête pendant 24 h, ou plus si `Retry-After` le demande ;
  - supprimer le fichier pour forcer la reprise.
- **Un seul nouvel essai**, uniquement sur erreur réseau ou 5xx.
- **Pas de scrutation** : rien n'est envoyé avant l'ouverture, sauf les connexions et les lectures.

## Installation (VPS)

```sh
sudo useradd -r -m -d /opt/clubbot clubbot
# copier le projet dans /opt/clubbot (sans .env, .venv, main.*.js)
cd /opt/clubbot
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
cp config.example.yaml config.yaml
cp .env.example .env && chmod 600 .env   # renseigner les identifiants
```

Premier essai sans réserver. Il envoie 3 requêtes : connexion, planning, réservations.

```sh
.venv/bin/python -m clubbot --dry-run --no-wait
```

### Planification

Avec un timer systemd (recommandé, l'heure de Paris est gérée même si le VPS est en UTC) :

```sh
sudo cp deploy/clubbot.service deploy/clubbot.timer /etc/systemd/system/
sudo systemctl daemon-reload && sudo systemctl enable --now clubbot.timer
journalctl -u clubbot   # journaux
```

Avec cron, uniquement si le VPS est réglé sur `Europe/Paris` (`timedatectl`) :

```cron
50 7 * * * cd /opt/clubbot && .venv/bin/python -m clubbot >> state/clubbot.log 2>&1
```

## Docker

L'image tourne en service : `--every-day 07:50` lance un passage chaque jour à 07:50, heure de Paris. Les identifiants sont fournis au démarrage par `env_file`, ils ne sont jamais dans l'image. Le verrou du coupe-circuit est conservé dans le volume `clubbot-state`.

```sh
docker build --target test -t clubbot:test .     # tests hors ligne pendant le build
docker build -t clubbot:latest .
docker run --rm clubbot:latest --help

cp config.example.yaml config.yaml               # requis : monté dans le conteneur
docker compose run --rm clubbot --dry-run --no-wait   # 1er essai réel, 3 requêtes, aucune réservation
docker compose up -d && docker compose logs -f
```

Pour lever un verrou du coupe-circuit :

```sh
docker compose run --rm --entrypoint rm clubbot /app/state/blocked.json
```

## Options

| Option | Effet |
|---|---|
| `--dry-run` | tout faire sauf envoyer les réservations |
| `--no-wait` | ne pas attendre l'ouverture (avec `--dry-run`, ou pour un cours déjà ouvert) |
| `--date AAAA-MM-JJ` | viser un autre jour que J+14 |
| `--every-day HH:MM` | rester actif et faire un passage chaque jour à cette heure (mode Docker) |
| `-v` | journal détaillé |

Codes de sortie :
- 0 : tout est réservé ou rien n'était à faire ;
- 1 : au moins un échec ou un cours complet ;
- 2 : arrêt de sécurité ;
- 3 : erreur de configuration.

## Notifications

- `ACCOUNT_<NOM>_WEBHOOK` (optionnel, un par compte) : reçoit le résultat des seuls cours de ce compte.
  - Méthode POST par défaut, ou PUT avec `ACCOUNT_<NOM>_WEBHOOK_METHOD=PUT`.
  - Corps JSON : `{title, message, level, account, date, outcomes: [{course, status, detail}]}`.
  - `status` vaut par exemple `réservé`, `liste d'attente`, `déjà inscrit`, `absent du planning`, `complet` ou `échec`.
- `NOTIFY_WEBHOOK_URL` (optionnel) : résumé de tous les comptes, envoyé en POST `{title, message, level}`. Sans cette variable, le résumé est seulement écrit dans le journal.
- Un arrêt de sécurité est signalé à tous les webhooks, avec `level: error`.

C'est compatible avec Home Assistant : dans l'automatisation déclenchée par le webhook, utiliser `{{ trigger.json.message }}` dans la notification.

## Tests

Les tests tournent entièrement hors ligne, sur un faux site construit à partir de réponses réelles (`tests/fixtures`).

```sh
.venv/bin/pip install pytest && .venv/bin/pytest -q
```
