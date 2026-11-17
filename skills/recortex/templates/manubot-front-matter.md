{##
  The front matter of a manuscript kept by recortex. Pandoc inserts the title from metadata.yaml. This is the plugin's
  own short template; it uses only the variables that Manubot's own front-matter template uses (manubot.authors,
  manubot.ci_source, manubot.html_url_versioned and manubot.generated_date_long).
##}

<small><em>
This manuscript
{% if manubot.html_url_versioned is defined -%}
([permalink]({{manubot.html_url_versioned}}))
{% endif -%}
was automatically generated
{% if manubot.ci_source is defined -%}
from [{{manubot.ci_source.repo_slug}}@{{manubot.ci_source.commit | truncate(length=7, end='', leeway=0)}}](https://github.com/{{manubot.ci_source.repo_slug}}/tree/{{manubot.ci_source.commit}})
{% endif -%}
on {{manubot.generated_date_long}}.
</em></small>

{% if manubot.authors %}
## Authors
{% for author in manubot.authors %}
+ **{{author.name}}**
{% endfor %}
{% endif %}
