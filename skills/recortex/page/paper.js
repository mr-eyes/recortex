(function () {
  'use strict';
  // Fixed text, for paper.html only. No record text lives here, and nothing is loaded or sent: the one button of the page
  // asks the browser to print it, and the browser's own dialog offers "Save as PDF".
  document.documentElement.classList.add('js');
  document.addEventListener('click', function (event) {
    var button = event.target && event.target.closest ? event.target.closest('[data-print]') : null;
    if (button && window.print) { window.print(); }
  });
}());
