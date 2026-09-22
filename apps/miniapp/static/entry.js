/* Resolve public entry before rendering. Bot links never fall back to sample identity. */
(() => {
  const url=new URL(location.href);
  if(!url.searchParams.has('bot_id')&&url.searchParams.get('preview')!=='1'){
    url.searchParams.set('preview','1');
    location.replace(url.href);
  }
})();
