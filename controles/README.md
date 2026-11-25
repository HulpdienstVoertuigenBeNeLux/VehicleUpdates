# controles/

Controles op de sheets die in de uurlijkse run (`run-hulpdienstvoertuigenbenelux.yml`) draaien.

- `kolom_o.py` — kolom O (DataType): rij met ID moet Locatie/Voertuig/- hebben, "-" mag geen foto hebben.
  Melding per land naar `DISCORD_WEBHOOK_URL_REGIONS`, rode voorwaardelijke opmaak in de sheets via het serviceaccount.

Nieuwe sheet erbij: regel toevoegen in `config/sheets.json`, secret met het sheet-ID aanmaken, het secret in de
workflowstap doorgeven en de sheet als Bewerker delen met het serviceaccount.
