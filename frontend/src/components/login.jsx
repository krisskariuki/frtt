export default function Login(){
    return(
        <section className="page login-page">
            <form action="" id="login-form">

                <div id="casino-choice">
                    <label>
                    <span>Mozzartbet</span>
                    <input type="radio" name='casino' checked/>
                    </label>

                   {/* <label> 
                   <span>betika</span>
                    <input type="radio" name='casino'/>
                   </label> */}
                </div>
                <input type="tel" placeholder='Phone number'/>
                <input type="password" placeholder='Password'/>

                <button type='submit' id="submit-login">Log in</button>
            </form>
        </section>
    )
}