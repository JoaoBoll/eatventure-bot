# Configuration requise pour EatVenture AI/BOT

[English](../README.md#requirements) · [Português do Brasil](../README.pt-BR.md#requisitos) · Français

- Windows 11, Windows 10 22H2 ou Linux 64 bits, par exemple Ubuntu 24.04 LTS ou une version plus récente. Pour une nouvelle installation, privilégiez Windows 11 ou Linux. Le support standard de Windows 10 a pris fin le 14 octobre 2025. [Cycle de vie de Windows 10](https://support.microsoft.com/en-us/windows/deployment/updates-lifecycle/windows-10-support-has-ended-on-october-14-2025)
- Python 3.12 en 64 bits avec `pip` et `venv`. Installez les dépendances de [requirements.txt](../requirements.txt), ainsi que celles de [requirements-web.txt](../requirements-web.txt) si vous utilisez le tableau de bord.
- Pour jouer ou collecter des captures, utilisez un appareil Android compatible avec EatVenture, avec le débogage USB ou sans fil autorisé, ADB et [scrcpy](https://github.com/Genymobile/scrcpy) 1.23 ou ultérieur. Vous pouvez débrancher le téléphone pour entraîner le modèle à partir de captures enregistrées.

## Configuration matérielle pour le système et EatVentureAI

Ces estimations incluent le système d’exploitation et supposent **un téléphone**, une résolution de capture maximale de **1080 × 2400**, environ **10 000 images**, une mémoire de rejeu de **30 000 recadrages par résolution** et **200 arbres**. L’entraînement utilise le processeur. Une puce graphique intégrée suffit en usage courant.

| Environnement | Bot et tableau de bord, sans entraînement en cours | Bot, tableau de bord et entraînement sur la même machine |
| --- | --- | --- |
| Windows 10 ou 11 | 4 cœurs, 8 Go de RAM | 6 cœurs, 16 Go de RAM |
| Linux avec interface graphique | 4 cœurs, 8 Go de RAM | 6 cœurs, 16 Go de RAM |
| Linux sans interface graphique | 2 cœurs, 4 Go de RAM | 4 cœurs, 8 Go de RAM avec le bot en pause et peu de tâches d’entraînement en parallèle. 16 Go pour plus de marge. |

Sous Windows ou Linux avec interface graphique, la configuration recommandée est de **6 cœurs, 16 Go de RAM et un SSD de 256 Go**. Prévoyez **32 Go de RAM** pour faire tourner plusieurs bots, entraîner le modèle sans mettre le bot en pause ou augmenter la mémoire de rejeu jusqu’à environ 100 000 recadrages. Les performances dépendent du processeur, de la résolution, de la taille du lot et du nombre de tâches parallèles.

Pour commencer, mettez le bot en pause pendant l’entraînement. Dans le tableau de bord, réglez le paramètre **Processus d’entraînement** sur 1 ou 2 avec 8 Go de RAM, ou sur 2 à 4 avec 16 Go. Ajustez ensuite selon l’utilisation du processeur et de la mémoire.

Gardez **30 Go d’espace libre sur le SSD après l’installation du système et de l’application** pour les captures, le cache, les modèles et les journaux. À titre indicatif, 10 000 images de 300 à 600 Ko occupent de 3 à 6 Go. Les vecteurs de 30 000 recadrages en `float16` occupent environ 187 Mo sur le disque, hors index et métadonnées. L’historique des caractéristiques et le jeu de test grossissent avec les collectes et le nombre de résolutions. Prévoyez aussi de l’espace pour leurs sauvegardes.

Consultez les instructions détaillées pour [installer le projet](../README.md#setup), [connecter un appareil Android](../README.md#connect-android) et [lancer le bot ou le tableau de bord](../README.md#run-commands) (en anglais).
