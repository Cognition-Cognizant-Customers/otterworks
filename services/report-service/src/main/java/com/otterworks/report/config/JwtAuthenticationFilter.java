package com.otterworks.report.config;

import io.jsonwebtoken.Claims;
import io.jsonwebtoken.Jws;
import io.jsonwebtoken.JwtException;
import io.jsonwebtoken.Jwts;
import io.jsonwebtoken.security.Keys;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.security.authentication.UsernamePasswordAuthenticationToken;
import org.springframework.security.core.authority.SimpleGrantedAuthority;
import org.springframework.security.core.context.SecurityContextHolder;
import org.springframework.stereotype.Component;
import org.springframework.util.StringUtils;
import org.springframework.web.filter.OncePerRequestFilter;

import javax.annotation.PostConstruct;
import javax.crypto.SecretKey;
import javax.servlet.FilterChain;
import javax.servlet.ServletException;
import javax.servlet.http.HttpServletRequest;
import javax.servlet.http.HttpServletResponse;
import java.io.IOException;
import java.nio.charset.StandardCharsets;
import java.util.ArrayList;
import java.util.Collection;
import java.util.Collections;
import java.util.List;

@Component
public class JwtAuthenticationFilter extends OncePerRequestFilter {

    private static final Logger logger = LoggerFactory.getLogger(JwtAuthenticationFilter.class);

    @Value("${otterworks.auth.jwt-secret:}")
    private String jwtSecret;

    @PostConstruct
    public void logAuthenticationMode() {
        if (!StringUtils.hasText(jwtSecret)) {
            logger.error("Report service is misconfigured: report endpoints are disabled until JWT_SECRET is set.");
        }
    }

    @Override
    protected void doFilterInternal(
            HttpServletRequest request, HttpServletResponse response, FilterChain filterChain)
            throws ServletException, IOException {
        String userId = null;
        Collection<SimpleGrantedAuthority> authorities = Collections.emptyList();

        String authorization = request.getHeader("Authorization");
        if (StringUtils.hasText(jwtSecret)) {
            if (authorization != null && authorization.startsWith("Bearer ")) {
                try {
                    Jws<Claims> claims =
                            Jwts.parser()
                                    .verifyWith(signingKey())
                                    .build()
                                    .parseSignedClaims(authorization.substring("Bearer ".length()).trim());
                    userId = claims.getPayload().getSubject();
                    if (!StringUtils.hasText(userId)) {
                        userId = claims.getPayload().get("user_id", String.class);
                    }
                    authorities = authorities(claims.getPayload().get("roles"));
                } catch (JwtException | IllegalArgumentException e) {
                    logger.debug("JWT authentication failed: {}", e.getMessage());
                }
            }
        }

        if (StringUtils.hasText(userId)) {
            UsernamePasswordAuthenticationToken authentication =
                    new UsernamePasswordAuthenticationToken(userId, null, authorities);
            SecurityContextHolder.getContext().setAuthentication(authentication);
        }

        filterChain.doFilter(request, response);
    }

    private SecretKey signingKey() {
        return Keys.hmacShaKeyFor(jwtSecret.getBytes(StandardCharsets.UTF_8));
    }

    private Collection<SimpleGrantedAuthority> authorities(Object rolesClaim) {
        if (rolesClaim == null) {
            return Collections.emptyList();
        }

        List<String> roles = new ArrayList<>();
        if (rolesClaim instanceof Collection) {
            for (Object role : (Collection<?>) rolesClaim) {
                roles.add(String.valueOf(role));
            }
        } else {
            roles.add(String.valueOf(rolesClaim));
        }

        List<SimpleGrantedAuthority> authorities = new ArrayList<>();
        for (String role : roles) {
            if (StringUtils.hasText(role)) {
                authorities.add(new SimpleGrantedAuthority(
                        role.startsWith("ROLE_") ? role : "ROLE_" + role));
            }
        }
        return authorities;
    }
}
