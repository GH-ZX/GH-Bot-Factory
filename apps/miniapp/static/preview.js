/* Public design preview. Sample data only; no authenticated API request is made. */
export const previewMode=new URLSearchParams(location.search).get('preview')==='1';
const category=[{id:'digital',name:'Digital essentials'},{id:'gifts',name:'Gift cards'},{id:'services',name:'Services'}];
const products=[
  {id:'sample-digital',category_id:'digital',title:'Create something great',description:'A sample digital toolkit for your next project. This is a design example, not a product for sale.',metadata:{featured:true,delivery_eta:'Digital delivery',warranty_days:7,warranty_terms:'Sample warranty. Real purchase terms are set by the store.'},variants:[{id:'sample-digital-standard',title:'Starter collection',sku:'DEMO-01',price:'12.00',currency:'USD',stock_quantity:25},{id:'sample-digital-plus',title:'Complete collection',sku:'DEMO-02',price:'24.00',currency:'USD',stock_quantity:10}]},
  {id:'sample-gift',category_id:'gifts',title:'A little something for them',description:'Showcase gift cards and digital vouchers with clear choices and delivery information.',metadata:{badge:'SAMPLE',delivery_eta:'After confirmation'},variants:[{id:'sample-gift-25',title:'Gift card · 25',sku:'DEMO-03',price:'25.00',currency:'USD',stock_quantity:18}]},
  {id:'sample-service',category_id:'services',title:'Let us take care of it',description:'A sample service package. A real store can explain its process, required information and delivery window here.',metadata:{badge:'PERSONAL TOUCH'},variants:[{id:'sample-service-basic',title:'Essential service',sku:'DEMO-04',price:'39.00',currency:'USD',stock_quantity:8}]},
];
export async function previewAPI(path,options={}){
  if(options.method&&options.method!=='GET')throw new Error('Design preview only. Purchases, payments and messages are disabled.');
  const url=new URL(path,location.origin),route=url.pathname;
  if(route==='/api/v1/storefront/bootstrap')return {store:{id:'preview-store',name:'Your Store',slug:'preview',business_type:'GENERAL',enabled_modules:['catalog','orders','account'],settings:{miniapp_theme:new URLSearchParams(location.search).get('theme')||'emerald',locale:'en',store_tagline:'Good things, a few taps away.',store_description:'A storefront that feels at home in Telegram. Browse sample products, explore your account, and discover how customers get help.',faq:[{question:'Is this a real store?',answer:'No. This preview uses sample products and balances. All purchases and account changes are disabled.'},{question:'Can I choose another theme?',answer:'Yes. Use the theme selector above to explore the five palettes.'}]}},user:{id:'preview-customer',first_name:'Alex',username:'preview_customer'},wallets:[{currency:'USD',balance:'85.00'}],asset_wallets:[]};
  if(route==='/api/v1/storefront/catalog'){
    const query=(url.searchParams.get('q')||'').toLowerCase(),cat=url.searchParams.get('category_id');
    const filtered=products.filter(p=>(!cat||p.category_id===cat)&&(!query||`${p.title} ${p.description} ${p.variants.map(v=>v.sku).join(' ')}`.toLowerCase().includes(query))&&(url.searchParams.get('available')!=='false'));
    return {categories:category,products:filtered,total:filtered.length,has_more:false,next_offset:null};
  }
  if(route==='/api/v1/storefront/wallet/topups/options')return {providers:[{provider_name:'preview',display_name:'Sample payment method',currencies:['USD'],min_amount:'5',max_amount:'500',terms_required:false}]};
  if(route==='/api/v1/storefront/wallet/payment-methods')return {methods:[]};
  if(route.endsWith('/orders')||route.endsWith('/support'))return [];
  if(route.endsWith('-history'))return {items:[],next_offset:null};
  throw new Error('This action is unavailable in the design preview.');
}
export function bindPreview(){
  if(!previewMode)return;
  document.getElementById('previewBanner').classList.remove('hidden');
  const select=document.getElementById('previewTheme');
  select.value=new URLSearchParams(location.search).get('theme')||'emerald';
  select.onchange=()=>{const url=new URL(location.href);url.searchParams.set('theme',select.value);location.assign(url);};
}
