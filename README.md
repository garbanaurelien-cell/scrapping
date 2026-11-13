# Extraction d'images et simulation de connecteurs

Python 3.10+, aucune dependance, aucune API Decodo.

## Proxy reel fourni par l'utilisateur

Configurer un proxy HTTP fonctionnel (tunnel CONNECT pour les cibles HTTPS).
Les memes parametres sont utilises pour Chromium, les pages HTTP et les
telechargements. Les sorties fictives de SessionPool restent de la simulation.

```powershell
$env:SCRAPE_PROXY_SERVER = Read-Host 'Adresse du proxy au format http://hote:port'
$env:SCRAPE_PROXY_USERNAME = Read-Host 'Utilisateur (vide si inutile)'
$secret = Read-Host 'Mot de passe (vide si inutile)' -AsSecureString
$env:SCRAPE_PROXY_PASSWORD = [System.Net.NetworkCredential]::new('', $secret).Password
$page = Read-Host 'URL a tester'
python scrape.py --url $page --browser --download --limit 3
```

Aucun fournisseur ni IP de sortie n'est fourni. SOCKS n'est pas pris en charge
par cette configuration commune. Le serveur proxy doit etre saisi sans identifiants.
Les secrets ne sont pas inscrits dans les resultats. Les erreurs avec proxy sont
resumees pour eviter leur divulgation. Aucun nouvel essai direct apres un echec.
Les exceptions proxy du systeme/NO_PROXY et les regles navigateur peuvent toutefois
exclure certains hotes (notamment locaux). Ce n'est pas un tunnel VPN ni une garantie
d'anonymat. Verifier l'IP de sortie aupres du fournisseur avant usage.
Sans SCRAPE_PROXY_SERVER, les commandes avec --url sont bloquees par defaut.
Pour autoriser explicitement un test sans proxy, ajouter --allow-direct.
Le navigateur utilise un contexte neuf et non persistant a chaque execution,
sans import des cookies ou de l'historique du navigateur personnel.
Cela ne change pas l'empreinte du navigateur et ne garantit pas l'anonymat.
Pour desactiver : Remove-Item Env:SCRAPE_PROXY_SERVER, Env:SCRAPE_PROXY_USERNAME, Env:SCRAPE_PROXY_PASSWORD -ErrorAction SilentlyContinue

## Mode navigateur optionnel

```powershell
python -m pip install playwright
python -m playwright install chromium
python scrape.py --url $env:TEST_PAGE_URL --browser --download --limit 3
```

Definir TEST_PAGE_URL avec la page de test souhaitee. Aucune cible n'est codee
dans le mode navigateur. Playwright est facultatif pour le mode HTTP.
Le navigateur execute JavaScript, attend --wait-ms (2000 par defaut), puis
lit les sources des img dans le DOM, dont currentSrc pour srcset.
Bloquer le clic droit ou les raccourcis DevTools ne protege pas ces sources.
La session est neuve, sans connexion a un compte ni technique de camouflage.
Les refus HTTP interrompent la navigation. Ni CAPTCHA ni geoblocage ne sont contournes.
Les canvas, images CSS, blob:, iframes et parcours avec defilement ne sont pas extraits.
Le telechargement reste un GET separe : une image exigeant les cookies du
navigateur ou un referer peut echouer ; cet echec est inscrit dans le JSON.
Les deux modes precedents restent disponibles.

## Extraire et telecharger des images

```powershell
python scrape.py --url https://books.toscrape.com/ --download --limit 3
```

Le script recupere une page HTML, extrait les URL des balises img et leur texte
alternatif, resout les chemins relatifs et deduplique les URL. Il prend en charge
src et data-src, ainsi que la balise base. Aucun JavaScript n'est execute.
Les images CSS, srcset seul et data: ne sont pas extraits. Aucune pagination.

- Sans --download : liste des images uniquement, sans telechargement des fichiers.
- --limit 20 : maximum d'images retenues (1 a 100, 20 par defaut).
- --output runs/images.json : manifeste JSON, remplace s'il existe.
- --timeout 30 : delai par requete en secondes.

Les fichiers sont enregistres dans runs/images_files/ par defaut avec des noms
numerotes et une extension determinee par le Content-Type HTTP.
Une limite de 5 MB s'applique au HTML et de 10 MB a chaque image.
Chaque echec de telechargement est inscrit dans le manifeste ; les autres images
continuent. Le bilan indique le nombre de fichiers effectivement telecharges.
Les fichiers existants du meme nom peuvent etre remplaces ; les anciens fichiers
supplementaires ne sont pas supprimes. Le type MIME est verifie, pas le decodage
complet des images. Les telechargements sont sequentiels.

## Architecture

### Diagnostic visuel sans inspecteur

```powershell
$page = Read-Host 'URL a diagnostiquer'
python scrape.py --url $page --browser --wait-ms 10000 --diagnostics runs/diagnostic
```

`runs/diagnostic/page.png` montre le viewport du navigateur apres chargement.
Ajouter `--reject-cookies` pour cliquer sur un bouton visible et unique nomme
Reject All, Tout refuser ou Refuser tout. Aucun consentement positif automatique.
Si le bouton est absent ou ambigu, le script le signale sans masquer la fenetre.
Ajouter `--full-page` pour capturer toute la hauteur du document principal.
Utiliser `--scroll-steps 8` pour charger progressivement les images avant capture.
Avec les pauses par defaut, cela peut prendre plusieurs minutes.
Une capture pleine page n'inclut pas necessairement tout le contenu d'un lecteur
virtualise ou d'une zone de defilement interne.
`elements.json` liste les img et canvas du document principal, leurs sources,
dimensions affichees, dimensions naturelles des images et visibilite approximative.
Le rapport ne classe pas automatiquement les roles, ne traverse pas les iframes
et ne collecte pas les images CSS. Aucun telechargement individuel sans --download.
Les fichiers de diagnostic existants sont remplaces. Ils peuvent contenir des
URL temporaires ou du contenu personnel : les garder locaux.

### Pauses et defilement

Les telechargements sont espaces par une pause aleatoire de 3 a 22 secondes
par defaut, sans pause avant le premier. Le tirage utilise 80 % du temps la
partie basse (55 % de l'intervalle), et 20 % la partie haute. La moyenne theorique
est d'environ 10 secondes avec les bornes par defaut ; chaque execution varie.
En mode navigateur, --scroll-steps N
ajoute N defilements de 300 a 650 pixels avec les memes pauses, avant extraction.
Le defilement est desactive par defaut et limite a 30 etapes.
Ces options servent au rythme des requetes et au chargement differe ; elles ne
garantissent pas un comportement humain et ne contournent pas les protections.

```powershell
python scrape.py --url $page --browser --download --limit 3 --scroll-steps 5 --delay-min 3 --delay-max 22
```

Les delais doivent respecter 0 <= minimum <= maximum <= 60 secondes.
Utiliser deux delais a zero desactive les pauses.

CLI -> HttpTransport -> ImageExtractor -> telechargements optionnels -> JSON

Le mode HTTP utilise un User-Agent explicite, sans proxys ni profils simules.
Il ne contourne aucun controle d'acces et n'execute aucun navigateur.

La simulation reste disponible sans --url :

```powershell
python scrape.py --country FR --sessions 3
python scrape.py --country DE --sessions 1
```

SessionPool alterne des profils navigateur et sorties proxy fictifs.
SimulatedTransport renvoie une decision geographique locale, sans reseau.
Ces profils declaratifs n'emulent pas les empreintes TLS ou JavaScript.
Les anciens resultats sous runs/ sont conserves.
