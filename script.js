// script.js

// Wait for the DOM to fully load before running scripts
document.addEventListener('DOMContentLoaded', function () {
    console.log('Page loaded successfully!');

    // Grab the heading and paragraph elements
    const heading = document.querySelector('h1');
    const paragraph = document.querySelector('p');

    // Example 1: Change the heading text after 2 seconds
    setTimeout(function () {
        heading.textContent = 'Thanks for Visiting!';
    }, 10000);

    // Example 2: Change the paragraph color on click
    paragraph.addEventListener('click', function () {
        paragraph.style.color = paragraph.style.color === 'blue' ? 'black' : 'blue';
        paragraph.style.cursor = 'pointer';
    });

    // Example 3: Log a message when someone clicks anywhere on the page
    document.body.addEventListener('click', function (event) {
        console.log('You clicked at:', event.clientX, event.clientY);
    });
});
