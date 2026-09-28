# Sécurité

Ma Pixelothèque est en cours de construction. Le premier incrément couvre les comptes et les albums ; le durcissement de l’upload et des partages sera vérifié avec leur implémentation. Ne pas considérer cette étape comme une validation de tout le futur MVP pour une exposition publique.

## Signaler une vulnérabilité

Utiliser le formulaire privé « Report a vulnerability » de l’onglet Security du [dépôt](https://github.com/Macsim51/ma-pixelotheque/security), lorsqu’il est activé. S’il n’est pas disponible, demander un canal privé au mainteneur sans publier les détails de la faille. Aucun délai de réponse garanti n’est annoncé à ce stade.

Fournir la version, un scénario reproductible avec des données synthétiques et l’impact observé. Ne transmettre ni mot de passe, ni clé Django, ni base familiale, ni photos personnelles.

## Configuration de production

- `DJANGO_DEBUG=False`, secret unique hors du dépôt et `DJANGO_ALLOWED_HOSTS` explicites.
- HTTPS derrière un proxy contrôlé avant exposition Internet ; activer `APP_HTTPS=True` et `TRUST_PROXY_HTTPS=True` seulement avec les en-têtes proxy correctement remplacés.
- Aucun service HTTP ne doit publier les dossiers du code, des secrets, de la base, des médias ou des sauvegardes. WhiteNoise sert uniquement les statiques d’interface.
- Les seuls comptes administrateurs sont les superutilisateurs nécessaires ; pas d’inscription publique.
- Mettre à jour les correctifs Django/Python/dépendances après validation, et tester les restaurations.

Les versions exactes sont dans `requirements.txt` et `Dockerfile`. La branche Django 5.2 LTS est maintenue jusqu’en avril 2028 selon la [politique officielle](https://www.djangoproject.com/download/). Les versions antérieures du projet n’ont pas encore de politique de support distincte.
