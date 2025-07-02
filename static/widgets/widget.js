(function () {
  fetch("http://localhost:8000/health")
  .then(res => {

    if(!res.ok) throw new Error("Down");

    const iframe = document.createElement('iframe');
    iframe.src = "http://localhost:8000/chat-ui"; // <--- Change this to your production URL later
    iframe.style.position = "fixed";
    iframe.style.bottom = "20px";
    iframe.style.right = "20px";
    iframe.style.width = "350px";
    iframe.style.height = "500px";
    iframe.style.border = "none";
    iframe.style.zIndex = "1000";
    iframe.style.borderRadius = "10px";
    iframe.style.boxShadow = "0 4px 20px rgba(0,0,0,0.2)";
    document.body.appendChild(iframe);
  }).catch(()=>{
    alert("Chat service is currently unavailable")
  })
})();
