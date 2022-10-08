.. title: {{fullname}}

{% if fullname != 'main' %}
.. click:: {{fullname}}.cli:cmd_{{fullname}}
  :prog: kupferbootstrap {{fullname}}
  :nested: full


{% endif %}

.. click:: {% if fullname == 'main' %}main:cli{% else %}{{fullname}}:cmd_{{fullname}}{% endif %}
  :prog: kupferbootstrap {{fullname}}
  :nested: full
